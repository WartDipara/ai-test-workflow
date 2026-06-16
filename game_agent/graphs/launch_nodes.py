"""LangGraph 进入游戏流程节点（复用现有 service）。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime

from game_agent.graphs.launch_deps import LaunchGraphDeps
from game_agent.graphs.launch_facts import (
    classify_screen_facts,
    interpretation_focus_for_facts,
    merge_analyze_screen_response,
    merge_interpretation_into_facts,
    needs_async_vision_enrichment,
    needs_sync_interpretation,
)
from game_agent.graphs.launch_routing import plan_route
from game_agent.graphs.launch_state_store import (
    clear_failed_node,
    get_last_ocr,
    increment_enter_tapped,
    mark_tree_node_done,
    mark_tree_node_failed,
    set_in_game_confirmed,
    set_login_done,
    set_privacy_checked,
    set_server_checked,
    set_sub_account_selected,
)
from game_agent.services.login_batch_fill import atomic_login_fill_and_submit
from game_agent.services.login_secure_keyboard import (
    LOGIN_BLACK_SCREENCAP_HINT,
    is_login_flow_in_progress,
    is_login_secure_keyboard_blackout,
    try_dismiss_login_secure_keyboard,
)
from game_agent.services.vision_tools import run_analyze_screen
from game_agent.services.screen_interpreter import interpret_launch_screen
from game_agent.services.node_verifier import verify_stage_exit
from game_agent.models.launch_graph_state import (
    MAX_GRAPH_ITERATIONS,
    LaunchGraphState,
)
from game_agent.services.credentials import load_game_credentials
from game_agent.services.dismiss_overlay import dismiss_overlay
from game_agent.services.blocking_overlay import (
    overlay_still_visible,
    resolve_dismiss_target,
    verify_overlay_dismissed,
)
from game_agent.services.game_entry_check import run_in_game_check
from game_agent.services.gameturbo_log import format_latest_gameturbo_log_for_agent
from game_agent.services.privacy_checkbox import ensure_privacy_checkbox_checked_multimodal
from game_agent.services.server_selector_pipeline import (
    message_indicates_e2006,
    run_full_server_selector_check,
)
from game_agent.utils.ocr_util import (
    deserialize_bboxes,
    run_ocr_frame,
    serialize_bboxes,
)

logger = logging.getLogger(__name__)

_CONTINUE_RE = re.compile(r"继续|确定|确认|retry|重试|continue|ok", re.IGNORECASE)


async def observe_screen(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    if deps.vision_queue is not None:
        state = deps.vision_queue.merge_if_ready(state)
    if deps.attempt_context is not None and deps.attempt_context.should_stop_executor():
        reason = deps.attempt_context.get_fatal_reason() or "monitor stop"
        state["finished"] = True
        state["terminal_error"] = reason[:2000]
        return state  # type: ignore[return-value]
    state["iteration"] = int(state.get("iteration") or 0) + 1
    if state["iteration"] >= MAX_GRAPH_ITERATIONS:
        state["finished"] = True
        state["terminal_error"] = f"launch graph iteration limit ({MAX_GRAPH_ITERATIONS})"
        return state  # type: ignore[return-value]
    sw, sh = deps.adb.touch_size()
    deps.screen_width, deps.screen_height = sw, sh
    ts = datetime.now().strftime("%H%M%S_%f")
    shot = deps.artifact_root / f"graph_observe_{ts}.png"
    deps.adb.screencap_png(shot)
    worker_key = deps.adb.device_serial
    actx = deps.attempt_context
    if actx is not None:
        actx.set_ocr_busy(True)
    try:
        ocr_summary, bboxes = await asyncio.to_thread(
            run_ocr_frame,
            shot,
            device_w=sw,
            device_h=sh,
            worker_key=worker_key,
        )
    finally:
        if actx is not None:
            actx.set_ocr_busy(False)
    if is_login_flow_in_progress(state) and is_login_secure_keyboard_blackout(
        shot,
        bboxes,
        ocr_summary=ocr_summary,
    ):
        logger.warning(
            "[LaunchGraph:observe] login secure keyboard blackout — %s",
            LOGIN_BLACK_SCREENCAP_HINT,
        )
        dismiss_msg = await asyncio.to_thread(
            try_dismiss_login_secure_keyboard,
            deps.adb,
            deps.app_config.executor,
        )
        deps.adb.wait_seconds(0.5)
        ts2 = datetime.now().strftime("%H%M%S_%f")
        shot = deps.artifact_root / f"graph_observe_after_kb_{ts2}.png"
        deps.adb.screencap_png(shot)
        if actx is not None:
            actx.set_ocr_busy(True)
        try:
            ocr_summary, bboxes = await asyncio.to_thread(
                run_ocr_frame,
                shot,
                device_w=sw,
                device_h=sh,
                worker_key=worker_key,
            )
        finally:
            if actx is not None:
                actx.set_ocr_busy(False)
        state["recover_hint"] = f"login secure keyboard dismissed: {dismiss_msg[:300]}"
    gameturbo = await asyncio.to_thread(
        format_latest_gameturbo_log_for_agent,
        deps.artifact_root.parent if deps.artifact_root.name == "executor" else deps.artifact_root,
        deps.adb,
        limit=80,
        refresh_from_device=True,
        include_health_hint=False,
    )
    state["last_screenshot"] = str(shot.resolve())
    state["last_ocr_summary"] = ocr_summary
    state["last_bboxes"] = serialize_bboxes(bboxes)
    state["gameturbo_summary"] = gameturbo
    return state  # type: ignore[return-value]


async def classify_screen(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    sw, sh = deps.screen_width, deps.screen_height
    shot_path = state.get("last_screenshot") or ""
    if not shot_path:
        state["facts"] = {}
        plan_route(state)
        return state  # type: ignore[return-value]
    from pathlib import Path

    shot = Path(shot_path)
    bboxes = deserialize_bboxes(state.get("last_bboxes"))
    if not bboxes:
        _, bboxes = await asyncio.to_thread(
            run_ocr_frame,
            shot,
            device_w=sw,
            device_h=sh,
            worker_key=deps.adb.device_serial,
        )
    facts = classify_screen_facts(
        bboxes,
        screen_w=sw,
        screen_h=sh,
        ocr_summary=state.get("last_ocr_summary") or "",
    )
    ocr_merged = state.get("last_ocr_summary") or ""
    ocr_has_sub_coords = facts.sub_account_action_xy is not None
    interpret_hash = ""
    try:
        interpret_hash = hashlib.sha256(shot.read_bytes()).hexdigest()[:16]
    except OSError:
        pass
    already_interpreted = interpret_hash and interpret_hash == state.get("interpret_screenshot_hash")
    if needs_sync_interpretation(facts, ocr_merged=ocr_merged) and not already_interpreted:
        focus = interpretation_focus_for_facts(facts)
        interp = await interpret_launch_screen(
            llm_cfg=deps.app_config.llm_multimodal,
            screenshot_path=shot,
            ocr_summary=ocr_merged,
            focus=focus,
            round_id=deps.round_id,
        )
        facts = merge_interpretation_into_facts(
            facts,
            interp,
            ocr_has_sub_account_coords=ocr_has_sub_coords,
        )
        if interpret_hash:
            state["interpret_screenshot_hash"] = interpret_hash
        logger.info(
            "[LaunchGraph:classify] sync interpret stage=%s tap=%s",
            interp.stage,
            facts.sub_account_action_xy or facts.announcement_dismiss_xy,
        )
    state["facts"] = facts.model_dump()
    if deps.vision_queue is not None and needs_async_vision_enrichment(facts):
        deps.vision_queue.submit(
            Path(shot_path),
            state.get("last_ocr_summary") or "",
        )
        state["pending_vision_path"] = shot_path
        state["vision_enrichment_status"] = "pending"
    state["current_stage"] = _infer_stage_label(facts, state)
    deps.run_state.launch_stage = state["current_stage"]
    if deps.attempt_context is not None:
        deps.attempt_context.set_ui_observation(state["current_stage"])
    plan_route(state)
    return state  # type: ignore[return-value]


def _infer_stage_label(facts, state: LaunchGraphState) -> str:
    if state.get("in_game_confirmed"):
        return "in_game"
    if facts.login_blocking:
        return "login_form"
    if facts.sub_account_blocking:
        return "sub_account_select"
    if facts.download_visible:
        return "download"
    if facts.initial_privacy_dialog:
        return "privacy"
    if facts.server_slot_visible and not state.get("server_checked"):
        return "server_select"
    if facts.terms_checkbox_visible and not state.get("privacy_checked"):
        return "privacy_agree"
    if facts.enter_cta_visible:
        return "server_select"
    return "launch"


async def ensure_privacy_checkbox_node(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
) -> LaunchGraphState:
    state = dict(state)
    node = "ensure_privacy_checkbox"
    result = await ensure_privacy_checkbox_checked_multimodal(
        deps.adb,
        deps.artifact_root,
        llm_cfg=deps.app_config.llm_multimodal,
        molmopoint_cfg=deps.app_config.molmopoint,
        prefix="graph_privacy_cb",
        already_tapped=bool(state.get("privacy_checked")),
        round_id=deps.round_id,
    )
    if result.verified:
        set_privacy_checked(state)
        deps.run_state.privacy_checkbox_tapped = True
        mark_tree_node_done(state, node, artifact=str(result.debug_marked_image or ""))
    else:
        mark_tree_node_failed(state, node, result.message)
    return state  # type: ignore[return-value]


async def handle_initial_privacy_dialog_node(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
) -> LaunchGraphState:
    state = dict(state)
    node = "handle_initial_privacy_dialog"
    from game_agent.models.launch_graph_state import facts_from_state

    facts = facts_from_state(state)
    if facts.agree_button_xy is None:
        mark_tree_node_failed(state, node, "no agree button coords")
        return state  # type: ignore[return-value]
    x, y = facts.agree_button_xy
    sw, sh = deps.screen_width, deps.screen_height
    msg = deps.adb.tap(x, y, width=sw, height=sh)
    deps.adb.wait_seconds(0.8)
    mark_tree_node_done(state, node, artifact=msg)
    return state  # type: ignore[return-value]


async def handle_download_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    node = "handle_download"
    sw, sh = deps.screen_width, deps.screen_height
    shot_path = state.get("last_screenshot")
    tapped = False
    if shot_path:
        bboxes = deserialize_bboxes(state.get("last_bboxes"))
        if not bboxes:
            from pathlib import Path

            _, bboxes = await asyncio.to_thread(
                run_ocr_frame,
                Path(shot_path),
                device_w=sw,
                device_h=sh,
                worker_key=deps.adb.device_serial,
            )
        for bbox in bboxes:
            if _CONTINUE_RE.search(bbox.text.strip()):
                deps.adb.tap(bbox.cx, bbox.cy, width=sw, height=sh)
                tapped = True
                break
    if not tapped:
        deps.adb.wait_seconds(5.0)
    mark_tree_node_done(state, node)
    return state  # type: ignore[return-value]


async def dismiss_blocking_overlay_node(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
) -> LaunchGraphState:
    state = dict(state)
    node = "dismiss_blocking_overlay"
    from pathlib import Path
    from game_agent.models.launch_graph_state import facts_from_state

    facts = facts_from_state(state)
    sw, sh = deps.screen_width, deps.screen_height
    shot_path = state.get("last_screenshot") or ""
    if not shot_path:
        mark_tree_node_failed(state, node, "no screenshot for overlay dismiss")
        return state  # type: ignore[return-value]

    shot = Path(shot_path)
    ocr_before = state.get("last_ocr_summary") or ""
    bboxes = deserialize_bboxes(state.get("last_bboxes"))
    if not bboxes:
        _, bboxes = await asyncio.to_thread(
            run_ocr_frame,
            shot,
            device_w=sw,
            device_h=sh,
            worker_key=deps.adb.device_serial,
        )

    plan = await resolve_dismiss_target(
        llm_cfg=deps.app_config.llm_multimodal,
        screenshot_path=shot,
        ocr_summary=ocr_before,
        bboxes=bboxes,
        screen_w=sw,
        screen_h=sh,
        facts=facts,
        round_id=deps.round_id,
    )
    if plan is None:
        mark_tree_node_failed(state, node, "no dismiss plan for blocking overlay")
        return state  # type: ignore[return-value]

    tap_msg = deps.adb.tap(plan.x, plan.y, width=sw, height=sh)
    deps.adb.wait_seconds(0.8)

    ts = datetime.now().strftime("%H%M%S_%f")
    after_shot = deps.artifact_root / f"graph_overlay_dismiss_{ts}.png"
    deps.adb.screencap_png(after_shot)
    actx = deps.attempt_context
    if actx is not None:
        actx.set_ocr_busy(True)
    try:
        ocr_after, _ = await asyncio.to_thread(
            run_ocr_frame,
            after_shot,
            device_w=sw,
            device_h=sh,
            worker_key=deps.adb.device_serial,
        )
    finally:
        if actx is not None:
            actx.set_ocr_busy(False)
    verify = verify_overlay_dismissed(ocr_before, ocr_after)
    still_visible = overlay_still_visible(ocr_after)

    if verify.passed or not still_visible:
        facts = facts.model_copy(
            update={
                "announcement_overlay": False,
                "announcement_dismiss_xy": None,
            }
        )
        state["facts"] = facts.model_dump()
        state["last_screenshot"] = str(after_shot.resolve())
        state["last_ocr_summary"] = ocr_after
        mark_tree_node_done(
            state,
            node,
            artifact=tap_msg,
            evidence=f"{plan.method}: {verify.reason}",
        )
        logger.info(
            "[LaunchGraph:overlay] dismissed method=%s (%s,%s) %s",
            plan.method,
            plan.x,
            plan.y,
            verify.reason,
        )
    else:
        await asyncio.to_thread(dismiss_overlay, deps.adb.device_serial, sw, sh)
        mark_tree_node_failed(
            state,
            node,
            f"overlay still visible after {plan.method}: {verify.reason}",
            artifact=tap_msg,
        )
    return state  # type: ignore[return-value]


def _load_login_credentials(state: LaunchGraphState, deps: LaunchGraphDeps, node: str):
    try:
        return load_game_credentials(
            deps.app_config.credentials.file_path,
            settings_path=deps.settings_path,
        ), None
    except (FileNotFoundError, ValueError) as e:
        mark_tree_node_failed(state, node, str(e))
        return None, str(e)


async def atomic_login_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    node = "atomic_login"
    cfg = deps.app_config
    sw, sh = deps.screen_width, deps.screen_height
    cred, err = _load_login_credentials(state, deps, node)
    if err is not None:
        return state  # type: ignore[return-value]

    if deps.attempt_context is not None:
        deps.attempt_context.set_ui_observation("login_form")

    result = await asyncio.to_thread(
        atomic_login_fill_and_submit,
        deps.adb,
        username=cred.username,
        password=cred.password,
        executor=cfg.executor,
        artifact_root=deps.artifact_root,
        screen_width=sw,
        screen_height=sh,
        cached_login_xy=deps.run_state.cached_login_button_xy,
    )
    if result.targets is not None and result.targets.login_button_xy is not None:
        deps.run_state.cached_login_button_xy = result.targets.login_button_xy
        deps.run_state.cached_login_button_text = result.targets.login_text

    if not result.ok:
        mark_tree_node_failed(state, node, result.message[:500])
        return state  # type: ignore[return-value]

    set_login_done(state, evidence=result.message[:200])
    deps.run_state.launch_stage = "login_form"
    mark_tree_node_done(state, node, artifact=result.message[:200], evidence="atomic_login_ok")
    return state  # type: ignore[return-value]


async def select_sub_account_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    node = "select_sub_account"
    from game_agent.models.launch_graph_state import facts_from_state

    facts = facts_from_state(state)
    if facts.sub_account_action_xy is None:
        mark_tree_node_failed(state, node, "no sub-account entry to tap")
        return state  # type: ignore[return-value]
    x, y = facts.sub_account_action_xy
    sw, sh = deps.screen_width, deps.screen_height
    ocr_before = get_last_ocr(state)
    deps.adb.tap(x, y, width=sw, height=sh)
    deps.adb.wait_seconds(1.2)
    ts = datetime.now().strftime("%H%M%S_%f")
    path = deps.artifact_root / f"graph_subacct_{ts}.png"
    deps.adb.screencap_png(path)
    actx = deps.attempt_context
    if actx is not None:
        actx.set_ocr_busy(True)
    try:
        ocr_after, _ = await asyncio.to_thread(
            run_ocr_frame,
            path,
            device_w=sw,
            device_h=sh,
            worker_key=deps.adb.device_serial,
        )
    finally:
        if actx is not None:
            actx.set_ocr_busy(False)
    verify = verify_stage_exit(
        ocr_before=ocr_before,
        ocr_after=ocr_after,
        expected_stage="sub_account_select",
        completion_signals=facts.screen_completion_signals,
    )
    if verify.passed:
        set_sub_account_selected(state, evidence=verify.evidence)
        deps.run_state.launch_stage = "server_select"
        mark_tree_node_done(
            state,
            node,
            artifact=str(path.resolve()),
            evidence=verify.evidence,
        )
    else:
        mark_tree_node_failed(
            state,
            node,
            verify.reason,
            artifact=str(path.resolve()),
            evidence=verify.evidence,
        )
    return state  # type: ignore[return-value]


async def check_server_selector_node(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
) -> LaunchGraphState:
    state = dict(state)
    node = "check_server_selector"
    result = await run_full_server_selector_check(
        deps.adb,
        deps.artifact_root,
        deps.app_config,
        round_id=deps.round_id,
    )
    deps.run_state.server_check_attempts += result.taps_used
    if result.ok and result.panel_opened:
        set_server_checked(state)
        deps.run_state.server_checked = True
        deps.run_state.launch_stage = "server_select"
        mark_tree_node_done(state, node)
    elif message_indicates_e2006(result.message):
        state["terminal_error"] = result.message[:2000]
        state["finished"] = True
        deps.run_state.finished = True
        deps.run_state.success = False
        deps.run_state.note = result.message[:2000]
        mark_tree_node_failed(state, node, result.message)
    else:
        mark_tree_node_failed(state, node, result.message)
    return state  # type: ignore[return-value]


async def tap_enter_game_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    node = "tap_enter_game"
    from game_agent.models.launch_graph_state import facts_from_state

    facts = facts_from_state(state)
    if facts.enter_cta_xy is None:
        mark_tree_node_failed(state, node, "no enter CTA")
        return state  # type: ignore[return-value]
    x, y = facts.enter_cta_xy
    sw, sh = deps.screen_width, deps.screen_height
    msg = deps.adb.tap(x, y, width=sw, height=sh)
    deps.adb.wait_seconds(1.0)
    increment_enter_tapped(state)
    mark_tree_node_done(state, node, artifact=msg)
    return state  # type: ignore[return-value]


async def check_in_game_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    node = "check_in_game"
    actx = deps.attempt_context
    result = await run_in_game_check(
        adb=deps.adb,
        cfg=deps.app_config,
        run_state=deps.run_state,
        artifact_root=deps.artifact_root,
        audit=deps.audit,
        round_id=deps.round_id,
        sessions_restarted=actx.session_restarts if actx else 0,
        session_index=actx.get_session_index() if actx else 1,
    )
    if result.confirmed:
        set_in_game_confirmed(state, evidence=result.message[:500])
        deps.run_state.in_game_confirmed = True
        deps.run_state.finished = True
        deps.run_state.success = True
        deps.run_state.note = result.message[:2000]
        deps.run_state.launch_stage = "in_game"
        if actx is not None:
            actx.signal_in_game_confirmed(deps.run_state.note)
        mark_tree_node_done(state, node)
    else:
        mark_tree_node_failed(state, node, result.message[:500])
    return state  # type: ignore[return-value]


async def recover_from_failure_node(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
) -> LaunchGraphState:
    state = dict(state)
    node = "recover_from_failure"
    root = deps.artifact_root.parent if deps.artifact_root.name == "executor" else deps.artifact_root
    gameturbo = await asyncio.to_thread(
        format_latest_gameturbo_log_for_agent,
        root,
        deps.adb,
        limit=100,
        refresh_from_device=True,
        include_health_hint=False,
    )
    state["gameturbo_summary"] = gameturbo
    from game_agent.models.launch_graph_state import facts_from_state

    facts = facts_from_state(state)
    last_failed = (state.get("failed_nodes") or {})
    failed_name = next(iter(last_failed.keys()), "unknown")
    hint = f"recover after {failed_name}"
    failed_detail = ""
    if failed_name in last_failed:
        failed_detail = str(last_failed[failed_name].get("last_error", ""))[:200]

    def _clear_failed(node_name: str) -> None:
        clear_failed_node(state, node_name)

    if is_login_flow_in_progress(state):
        sw, sh = deps.screen_width, deps.screen_height
        if not sw or not sh:
            sw, sh = deps.adb.touch_size()
            deps.screen_width, deps.screen_height = sw, sh

        ts = datetime.now().strftime("%H%M%S_%f")
        shot = deps.artifact_root / f"graph_recover_{ts}.png"
        deps.adb.screencap_png(shot)
        actx = deps.attempt_context
        if actx is not None:
            actx.set_ocr_busy(True)
        try:
            ocr_summary, bboxes = await asyncio.to_thread(
                run_ocr_frame,
                shot,
                device_w=sw,
                device_h=sh,
                worker_key=deps.adb.device_serial,
            )
        finally:
            if actx is not None:
                actx.set_ocr_busy(False)
        state["last_screenshot"] = str(shot.resolve())
        state["last_ocr_summary"] = ocr_summary
        state["last_bboxes"] = serialize_bboxes(bboxes)

        login_blackout = is_login_secure_keyboard_blackout(
            shot,
            bboxes,
            ocr_summary=ocr_summary,
        )
        if login_blackout:
            dismiss_msg = await asyncio.to_thread(
                try_dismiss_login_secure_keyboard,
                deps.adb,
                deps.app_config.executor,
            )
            hint = (
                f"{hint}; login secure keyboard blackout — {LOGIN_BLACK_SCREENCAP_HINT} "
                f"dismiss={dismiss_msg[:200]}"
            )
            logger.warning("[LaunchGraph:recover] %s", hint[:400])

        cfg = deps.app_config
        try:
            cred = load_game_credentials(
                cfg.credentials.file_path,
                settings_path=deps.settings_path,
            )
        except (FileNotFoundError, ValueError) as e:
            hint += f"; blind recover credentials error: {e}"
        else:
            result = await asyncio.to_thread(
                atomic_login_fill_and_submit,
                deps.adb,
                username=cred.username,
                password=cred.password,
                executor=cfg.executor,
                artifact_root=deps.artifact_root,
                screen_width=sw,
                screen_height=sh,
                cached_login_xy=deps.run_state.cached_login_button_xy,
            )
            if result.targets is not None and result.targets.login_button_xy is not None:
                deps.run_state.cached_login_button_xy = result.targets.login_button_xy
                deps.run_state.cached_login_button_text = result.targets.login_text
            if result.ok:
                set_login_done(state, evidence="blind retry atomic_login OK")
                deps.run_state.launch_stage = "login_form"
                _clear_failed("atomic_login")
                hint += "; blind retry atomic_login OK"
            else:
                hint += f"; blind retry atomic_login failed: {result.message[:200]}"

        hint += "; skip analyze_screen (login blind recover)"
        state["recover_hint"] = hint
        logger.info(
            "[LaunchGraph:recover] skip vision — login blind recover failed=%s blackout=%s",
            failed_name,
            login_blackout,
        )
        deps.adb.wait_seconds(0.5)
        mark_tree_node_done(state, node)
        return state  # type: ignore[return-value]

    skip_vision = (
        (facts.login_blocking and not state.get("login_done"))
        or failed_name == "atomic_login"
    )

    if deps.app_config.llm_multimodal is not None and not skip_vision:
        now = time.monotonic()
        last_analyze = float(state.get("last_analyze_screen_ts") or 0.0)
        if now - last_analyze < 30.0:
            hint += "; skip analyze_screen (cooldown)"
            logger.info("[LaunchGraph:recover] skip vision — analyze_screen cooldown")
        else:
            analyze_reason = f"recover:{failed_name}"
            if failed_detail:
                analyze_reason += f" — {failed_detail}"
            analyze_json = await run_analyze_screen(
                adb=deps.adb,
                cfg=deps.app_config,
                artifact_root=deps.artifact_root,
                round_id=deps.round_id,
                reason=analyze_reason,
                attempt_context=deps.attempt_context,
                audit=deps.audit,
            )
            state["last_analyze_screen_ts"] = now
            state["last_vision_summary"] = analyze_json
            facts, vision_hint = merge_analyze_screen_response(facts, analyze_json)
            state["facts"] = facts.model_dump()
            hint = f"{hint}; {vision_hint}"
            logger.info("[LaunchGraph:recover] analyze_screen | %s", vision_hint)
    elif skip_vision:
        hint += "; skip analyze_screen (login recover)"
        logger.info("[LaunchGraph:recover] skip vision — login_blocking=%s failed=%s", facts.login_blocking, failed_name)

    state["recover_hint"] = hint

    if facts.announcement_overlay:
        sw, sh = deps.screen_width, deps.screen_height
        if facts.announcement_dismiss_xy is not None:
            dx, dy = facts.announcement_dismiss_xy
            deps.adb.tap(dx, dy, width=sw, height=sh)
            hint += f"; tapped announcement dismiss ({dx},{dy})"
        else:
            await asyncio.to_thread(dismiss_overlay, deps.adb.device_serial, sw, sh)

    deps.adb.wait_seconds(0.5)
    mark_tree_node_done(state, node)
    return state  # type: ignore[return-value]
