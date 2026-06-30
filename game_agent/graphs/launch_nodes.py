"""LangGraph 进入游戏流程节点（复用现有 service）。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from game_agent.graphs.launch_deps import LaunchGraphDeps
from game_agent.graphs.launch_facts import (
    classify_screen_facts,
    interpretation_focus_for_facts,
    merge_analyze_screen_response,
    merge_interpretation_into_facts,
    merge_sticky_gate_facts,
    needs_async_vision_enrichment,
    needs_sync_interpretation,
)
from game_agent.graphs.launch_routing import plan_route
from game_agent.graphs.launch_phase import (
    clear_game_entry_judgment,
    in_game_entry_allowed,
    ocr_credential_login_passed,
    store_game_entry_judgment,
    vlm_login_verify_passed,
)
from game_agent.graphs.launch_limits import launch_graph_limits_from_state, seed_launch_graph_limits
from game_agent.graphs.launch_state_store import (
    clear_failed_node,
    completed_tree_node,
    get_last_ocr,
    increment_enter_tapped,
    is_privacy_checked,
    is_login_done,
    mark_tree_node_done,
    mark_tree_node_failed,
    set_in_game_confirmed,
    set_login_done,
    set_privacy_checked,
    set_server_checked,
    set_sub_account_selected,
)
from game_agent.services.enter_gate_planner import (
    decide_enter_gate_tap,
    enter_gate_likely_visible,
)
from game_agent.services.login_batch_fill import atomic_login_fill_and_submit
from game_agent.services.login_secure_keyboard import (
    LOGIN_BLACK_SCREENCAP_HINT,
    blackout_hint_for_state,
    blackout_streak,
    bump_blackout_streak,
    is_login_flow_in_progress,
    is_secure_keyboard_blackout,
    reset_blackout_streak,
    should_handle_secure_keyboard_blackout,
    should_press_back_for_blackout,
    try_dismiss_login_secure_keyboard,
    try_dismiss_secure_keyboard,
)
from game_agent.services.vision_tools import run_analyze_screen
from game_agent.services.screen_interpreter import interpret_launch_screen
from game_agent.services.node_verifier import NodeVerifyResult, verify_stage_exit
from game_agent.services.action_frame import note_action_failure, run_action_frame
from game_agent.models.launch_graph_state import (
    LaunchGraphState,
    facts_from_state,
)
from game_agent.services.credentials import (
    load_game_credentials,
    resolve_sub_account_match_phrases,
    sub_account_target_display,
    try_load_game_credentials,
)
from game_agent.services.dismiss_overlay import dismiss_overlay
from game_agent.services.blocking_overlay import (
    overlay_still_visible,
    recover_exit_confirm_dialog_if_present,
    resolve_dismiss_target,
    verify_overlay_dismissed,
)
from game_agent.services.game_entry_check import run_in_game_check, run_in_game_check_on_capture
from game_agent.core.external_log import fetch_external_log_summary
from game_agent.services.behavior_chain import (
    behavior_progress_fingerprint,
    evaluate_step_success,
    execute_behavior_step,
    ocr_progressed,
    press_back_caused_exit_confirm,
    sanitize_dialogue_blank_tap,
    sanitize_press_back_step,
)
from game_agent.services.in_game_agent import (
    advance_in_game_behavior_cursor,
    clear_in_game_behavior_chain,
    get_current_in_game_behavior_step,
    get_in_game_behavior_chain,
    mark_in_game_behavior_attempt,
    record_in_game_behavior_failure,
    set_in_game_behavior_chain,
)
from game_agent.services.in_game_brain_decision import apply_brain_decision_to_state
from game_agent.services.in_game_screen_analyze import run_in_game_screen_analyze_on_capture
from game_agent.services.in_game_session_planner import decide_in_game_session_round
from game_agent.services.in_game_stability_watch import run_stability_check
from game_agent.services.phase_engine import run_once as run_adaptive_phase_once
from game_agent.services.privacy_checkbox import ensure_privacy_checkbox_checked_multimodal
from game_agent.services.download_gate import (
    ocr_still_downloading,
    pick_continue_button_from_ocr,
    resolve_download_gate,
)
from game_agent.services.privacy_gate import resolve_privacy_gate
from game_agent.services.sub_account_gate import resolve_sub_account_gate
from game_agent.services.sub_account_locator import pick_sub_account_bbox
from game_agent.services.server_selector_pipeline import (
    message_indicates_e2006,
    run_full_server_selector_check_with_privacy_precheck,
)
from game_agent.models.run_failure import compact_failure_message
from game_agent.services.free_action_planner import (
    FreeActionPlan,
    compute_progress_fingerprint,
    decide_free_action,
)
from game_agent.services.dynamic_route_planner import (
    DynamicActionStep,
    advance_dynamic_cursor,
    can_replan_dynamic_chain,
    chain_progress_fingerprint,
    clear_dynamic_chain,
    get_current_step,
    mark_step_attempt,
    maybe_build_dynamic_chain,
    record_dynamic_chain_failure,
)
from game_agent.services.scene_classifier import classify_scene, detect_scene_transition
from game_agent.services.scene_gate import plan_from_scene_gate, resolve_scene_gate, scene_id_from_scene_gate
from game_agent.services.scene_strategies import (
    apply_scene_classification,
    is_pre_login_passive_wait,
    plan_loading_action,
    plan_scene_action,
)
from game_agent.models.scene import SceneActionPlan, SceneTransition
from game_agent.workers.vision_worker import VisionWorker
from game_agent.utils.in_game_hud_ocr import should_trigger_in_game_hud_check
from game_agent.utils.ocr_util import (
    deserialize_bboxes,
    run_ocr_frame,
    serialize_bboxes,
)

from game_agent.utils.ocr_util import OcrBbox
from game_agent.i18n import Concept, compile_lexicon_pattern

logger = logging.getLogger(__name__)

_CONTINUE_RE = compile_lexicon_pattern(Concept.CONTINUE, Concept.CONFIRM)


def _artifact_log_root(deps: LaunchGraphDeps) -> Path:
    if deps.artifact_root.name == "executor":
        return deps.artifact_root.parent
    return deps.artifact_root


async def _load_external_log_summary(
    deps: LaunchGraphDeps,
    *,
    limit: int = 80,
) -> str:
    return await fetch_external_log_summary(
        deps.external_log_reader,
        artifact_root=_artifact_log_root(deps),
        adb=deps.adb,
        limit=limit,
        refresh_from_device=True,
        include_health_hint=False,
    )


def _store_external_log_summary(state: LaunchGraphState, summary: str) -> None:
    state["external_log_summary"] = summary
    state["gameturbo_summary"] = summary


async def _try_confirm_in_game_via_hud_ocr(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
    *,
    ocr_summary: str,
    shot_path: Path,
    round_id: int,
    source: str,
) -> bool:
    """OCR HUD hit → multimodal in-game check. Returns True if entry passed."""
    if state.get("in_game_entry_passed") or state.get("in_game_confirmed"):
        return False
    from game_agent.models.launch_graph_state import facts_from_state

    facts = facts_from_state(state)
    if not in_game_entry_allowed(state, facts):
        return False
    trigger, hud_hits = should_trigger_in_game_hud_check(ocr_summary)
    if not trigger:
        return False
    actx = deps.attempt_context
    confirm_needed = deps.app_config.game.main_screen_confirm_rounds
    entry_result = await run_in_game_check_on_capture(
        shot_path=shot_path,
        ocr_summary=ocr_summary,
        cfg=deps.app_config,
        run_state=deps.run_state,
        audit=deps.audit,
        round_id=round_id,
        sessions_restarted=actx.session_restarts if actx else 0,
        session_index=actx.get_session_index() if actx else 1,
        confirm_needed=confirm_needed,
        provisional=True,
    )
    if entry_result.judgment is not None:
        store_game_entry_judgment(state, entry_result.judgment)
    if not entry_result.confirmed:
        return False
    state["in_game_entry_passed"] = True
    state["free_in_game_ocr_hits"] = hud_hits
    logger.info(
        "[LaunchGraph:%s] HUD trigger confirmed, route stability_observe | %s",
        source,
        hud_hits,
    )
    return True


def _in_game_play_session_active(state: LaunchGraphState) -> bool:
    if state.get("session_relogin_recovery_active"):
        return False
    if state.get("in_game_play_completed") or state.get("in_game_agent_done"):
        return False
    if state.get("session_agent_active"):
        return True
    started = float(
        state.get("in_game_play_started_at") or state.get("in_game_agent_started_at") or 0.0
    )
    return started > 0.0


def _foreground_poll_interval(deps: LaunchGraphDeps) -> float | None:
    fg = deps.app_config.foreground_guard
    return fg.poll_interval_s if fg.enabled else None


async def observe_screen(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    if not state.get("launch_graph_limits"):
        seed_launch_graph_limits(state, deps.app_config)
    limits = launch_graph_limits_from_state(state)
    if deps.vision_queue is not None:
        state = deps.vision_queue.merge_if_ready(state, attempt_context=deps.attempt_context)
    if deps.attempt_context is not None and deps.attempt_context.should_stop_executor():
        reason = deps.attempt_context.get_fatal_reason() or "monitor stop"
        state["finished"] = True
        state["terminal_error"] = reason[:2000]
        return state  # type: ignore[return-value]
    from game_agent.modules.run_context import block_until_foreground_ready

    fg_cfg = deps.app_config.foreground_guard
    if fg_cfg.enabled and not block_until_foreground_ready(
        deps.attempt_context,
        poll_interval_s=fg_cfg.poll_interval_s,
    ):
        reason = deps.attempt_context.get_fatal_reason() if deps.attempt_context else "monitor stop"
        state["finished"] = True
        state["terminal_error"] = (reason or "foreground guard stop")[:2000]
        return state  # type: ignore[return-value]

    if deps.attempt_context is not None and deps.attempt_context.consume_session_relogin_recovery():
        from game_agent.services.session_restart_recovery import apply_session_restart_to_state

        apply_session_restart_to_state(
            state,
            evidence="session_coordinator:process_restart",
            session_index=deps.attempt_context.get_session_index(),
        )
        deps.attempt_context.acknowledge_session_invalidation()
        if deps.audit is not None:
            restart_phase = state.get("session_restart_phase") or "unknown"
            deps.audit.log_phase(
                "session_restart",
                f"recovery_armed:{restart_phase}",
                session_index=deps.attempt_context.get_session_index(),
                restart_phase=restart_phase,
            )

    agent_active = _in_game_play_session_active(state) and not state.get("in_game_confirmed")
    if not agent_active:
        state["iteration"] = int(state.get("iteration") or 0) + 1
        if state["iteration"] >= limits.max_graph_iterations:
            state["finished"] = True
            state["terminal_error"] = f"launch graph iteration limit ({limits.max_graph_iterations})"
            return state  # type: ignore[return-value]
    actx = deps.attempt_context
    if actx is not None:
        actx.set_ocr_busy(True)
    try:
        ts = datetime.now().strftime("%H%M%S_%f")
        shot = deps.artifact_root / f"graph_observe_{ts}.png"
        deps.adb.screencap_png(shot)
        from game_agent.utils.screen_coord import (
            apply_screen_coord_to_state,
            resolve_screen_coord_space,
            sync_deps_screen_size,
        )

        space = resolve_screen_coord_space(deps.adb, shot)
        sync_deps_screen_size(deps, space)
        apply_screen_coord_to_state(state, space)
        sw, sh = space.tap_w, space.tap_h
        worker_key = deps.adb.device_serial
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
    if actx is not None and actx.is_session_invalidated():
        state["recover_hint"] = "observe:session_invalidated_skip_store"
        logger.warning("[LaunchGraph:observe] session invalidated — discard capture")
        return state  # type: ignore[return-value]
    if not state.get("in_game_confirmed") and not _in_game_play_session_active(state):
        recover_msg = recover_exit_confirm_dialog_if_present(
            deps.adb,
            bboxes,
            screen_w=sw,
            screen_h=sh,
        )
        if recover_msg:
            logger.info("[LaunchGraph:observe] %s", recover_msg[:160])
            deps.adb.wait_seconds(0.5)
            ts_rec = datetime.now().strftime("%H%M%S_%f")
            shot = deps.artifact_root / f"graph_observe_after_exit_cancel_{ts_rec}.png"
            deps.adb.screencap_png(shot)
            from game_agent.utils.screen_coord import resolve_and_sync

            space = resolve_and_sync(deps.adb, shot, deps=deps, state=state)
            sw, sh = space.tap_w, space.tap_h
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
            state["recover_hint"] = recover_msg[:300]
    blackout = is_secure_keyboard_blackout(shot, bboxes, ocr_summary=ocr_summary)
    if not blackout:
        reset_blackout_streak(state)
    elif is_login_flow_in_progress(state):
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
        from game_agent.utils.screen_coord import resolve_and_sync

        space = resolve_and_sync(deps.adb, shot, deps=deps, state=state)
        sw, sh = space.tap_w, space.tap_h
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
        if not is_secure_keyboard_blackout(shot, bboxes, ocr_summary=ocr_summary):
            reset_blackout_streak(state)
        state["recover_hint"] = f"login secure keyboard dismissed: {dismiss_msg[:300]}"
    elif should_handle_secure_keyboard_blackout(state):
        streak = bump_blackout_streak(state)
        hint = blackout_hint_for_state(state)
        logger.warning(
            "[LaunchGraph:observe] post-login secure keyboard blackout streak=%d — %s",
            streak,
            hint,
        )
        if should_press_back_for_blackout(streak):
            dismiss_msg = await asyncio.to_thread(
                try_dismiss_secure_keyboard,
                deps.adb,
                deps.app_config.executor,
                force_press_back=True,
            )
            deps.adb.wait_seconds(0.5)
            ts2 = datetime.now().strftime("%H%M%S_%f")
            shot = deps.artifact_root / f"graph_observe_after_kb_{ts2}.png"
            deps.adb.screencap_png(shot)
            from game_agent.utils.screen_coord import resolve_and_sync

            space = resolve_and_sync(deps.adb, shot, deps=deps, state=state)
            sw, sh = space.tap_w, space.tap_h
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
            if not is_secure_keyboard_blackout(shot, bboxes, ocr_summary=ocr_summary):
                reset_blackout_streak(state)
            state["recover_hint"] = (
                f"post-login keyboard blackout streak={streak} dismiss={dismiss_msg[:300]}"
            )
        elif streak == 2:
            deps.adb.wait_seconds(2.5)
            state["recover_hint"] = f"post-login keyboard blackout streak={streak} extra_wait"
        else:
            state["recover_hint"] = f"post-login keyboard blackout streak={streak} wait"
    external_summary = await _load_external_log_summary(deps, limit=80)
    state["last_screenshot"] = str(shot.resolve())
    state["last_ocr_summary"] = ocr_summary
    state["last_bboxes"] = serialize_bboxes(bboxes)
    _store_external_log_summary(state, external_summary)
    return state  # type: ignore[return-value]


async def classify_screen(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    from game_agent.modules.session_invalidation import capture_session_generation, discard_if_stale
    from game_agent.services.session_restart_recovery import clear_stale_scene_classify_scratch

    work_gen = capture_session_generation(deps.attempt_context)
    state["session_work_generation"] = work_gen
    classify_stale = False
    sw, sh = deps.screen_width, deps.screen_height
    shot_path = state.get("last_screenshot") or ""
    if not shot_path:
        state["facts"] = {}
        plan_route(state)
        return state  # type: ignore[return-value]

    shot = Path(shot_path)
    from game_agent.models.launch_graph_state import facts_from_state
    from game_agent.utils.screen_coord import resolve_and_sync

    if shot.is_file():
        space = resolve_and_sync(deps.adb, shot, deps=deps, state=state)
        sw, sh = space.tap_w, space.tap_h
    elif not sw or not sh:
        sw, sh = deps.adb.touch_size()
        deps.screen_width, deps.screen_height = sw, sh

    prev_facts = facts_from_state(state) if state.get("facts") else None
    bboxes = deserialize_bboxes(state.get("last_bboxes"))
    if not bboxes:
        _, bboxes = await asyncio.to_thread(
            run_ocr_frame,
            shot,
            device_w=sw,
            device_h=sh,
            worker_key=deps.adb.device_serial,
        )
    cred = try_load_game_credentials(
        deps.app_config.credentials.file_path,
        settings_path=deps.settings_path,
    )
    sub_phrases = resolve_sub_account_match_phrases(cred)
    sub_target = sub_account_target_display(cred)

    facts = classify_screen_facts(
        bboxes,
        screen_w=sw,
        screen_h=sh,
        ocr_summary=state.get("last_ocr_summary") or "",
        sub_account_phrases=sub_phrases,
    )
    ocr_merged = state.get("last_ocr_summary") or ""
    from game_agent.graphs.launch_facts import reinforce_login_from_vision
    from game_agent.services.login_stage_probe import split_screen_login_active_reason

    facts = reinforce_login_from_vision(
        facts,
        bboxes=bboxes,
        screen_w=sw,
        ocr_merged=ocr_merged,
    )
    if split_screen_login_active_reason(facts.classify_reason):
        state["split_screen_login"] = True
    privacy_milestones_done = (
        completed_tree_node(state, "handle_initial_privacy_dialog")
        and is_privacy_checked(state)
    )
    facts = await resolve_privacy_gate(
        facts,
        bboxes=bboxes,
        ocr_merged=ocr_merged,
        screen_w=sw,
        screen_h=sh,
        screenshot_path=shot,
        llm_cfg=deps.app_config.llm_multimodal,
        round_id=deps.round_id,
        privacy_milestones_done=privacy_milestones_done,
        attempt_context=deps.attempt_context,
    )
    if discard_if_stale(work_gen, where="classify:privacy_gate", ctx=deps.attempt_context):
        classify_stale = True
    facts = await resolve_sub_account_gate(
        facts,
        bboxes=bboxes,
        ocr_merged=ocr_merged,
        screen_w=sw,
        screen_h=sh,
        screenshot_path=shot,
        llm_cfg=deps.app_config.llm_multimodal,
        round_id=deps.round_id,
        sub_account_phrases=sub_phrases,
        sub_account_target=sub_target,
        attempt_context=deps.attempt_context,
    )
    if discard_if_stale(work_gen, where="classify:sub_account_gate", ctx=deps.attempt_context):
        classify_stale = True
    in_game_play_active = _in_game_play_session_active(state)
    skip_heavy_vlm = in_game_play_active
    llm_mm = deps.app_config.llm_multimodal
    if not skip_heavy_vlm:
        facts = await resolve_download_gate(
            facts,
            bboxes=bboxes,
            ocr_merged=ocr_merged,
            screenshot_path=shot,
            llm_cfg=llm_mm,
            round_id=deps.round_id,
            screen_h=sh,
            attempt_context=deps.attempt_context,
        )
        if discard_if_stale(work_gen, where="classify:download_gate", ctx=deps.attempt_context):
            classify_stale = True
    ocr_has_sub_coords = facts.sub_account_action_xy is not None
    interpret_hash = ""
    try:
        interpret_hash = hashlib.sha256(shot.read_bytes()).hexdigest()[:16]
    except OSError:
        pass
    already_interpreted = interpret_hash and interpret_hash == state.get("interpret_screenshot_hash")
    skip_interpret = (
        llm_mm is not None
        and llm_mm.in_game_skip_interpret
        and in_game_play_active
    )
    if (
        needs_sync_interpretation(facts, ocr_merged=ocr_merged)
        and not already_interpreted
        and not skip_interpret
    ):
        focus = interpretation_focus_for_facts(facts)
        interp = await interpret_launch_screen(
            llm_cfg=deps.app_config.llm_multimodal,
            screenshot_path=shot,
            ocr_summary=ocr_merged,
            focus=focus,
            round_id=deps.round_id,
            attempt_context=deps.attempt_context,
        )
        if discard_if_stale(work_gen, where="classify:interpret", ctx=deps.attempt_context):
            classify_stale = True
        else:
            facts = merge_interpretation_into_facts(
                facts,
                interp,
                ocr_has_sub_account_coords=ocr_has_sub_coords,
                ocr_merged=ocr_merged,
            )
            if interpret_hash:
                state["interpret_screenshot_hash"] = interpret_hash
            logger.info(
                "[LaunchGraph:classify] sync interpret stage=%s tap=%s",
                interp.stage,
                facts.sub_account_action_xy or facts.announcement_dismiss_xy,
            )
    if classify_stale:
        clear_stale_scene_classify_scratch(state)
        state["facts"] = facts.model_dump()
        state["recover_hint"] = "classify:stale_abort"
        state["session_work_generation"] = work_gen
        plan_route(state)
        logger.warning("[LaunchGraph:classify] stale_abort — skip scene classification")
        return state  # type: ignore[return-value]
    state["facts"] = facts.model_dump()
    if deps.vision_queue is not None and needs_async_vision_enrichment(facts):
        deps.vision_queue.submit(
            Path(shot_path),
            state.get("last_ocr_summary") or "",
            attempt_context=deps.attempt_context,
        )
        state["pending_vision_path"] = shot_path
        state["vision_enrichment_status"] = "pending"
    state["current_stage"] = _infer_stage_label(facts, state)
    deps.run_state.launch_stage = state["current_stage"]
    if deps.attempt_context is not None:
        deps.attempt_context.set_ui_observation(state["current_stage"])
    from game_agent.services.session_agent import ensure_session_agent_if_eligible

    ensure_session_agent_if_eligible(state)
    maybe_build_dynamic_chain(
        state,
        facts,
        bboxes,
        ocr_summary=ocr_merged,
    )
    scene_cls = classify_scene(
        facts,
        bboxes,
        ocr_summary=ocr_merged,
        screen_w=sw,
        screen_h=sh,
        screenshot_path=shot_path,
    )
    scene_cls, scene_gate_judgment = await resolve_scene_gate(
        state,
        scene_cls,
        facts=facts,
        bboxes=bboxes,
        ocr_merged=ocr_merged,
        screen_h=sh,
        screenshot_path=shot,
        llm_cfg=llm_mm,
        round_id=deps.round_id,
        screenshot_hash=interpret_hash,
        skip_vlm=skip_heavy_vlm,
        artifact_root=deps.artifact_root,
        scene_labels_cfg=deps.app_config.game.scene_labels,
        attempt_context=deps.attempt_context,
    )
    if discard_if_stale(work_gen, where="classify:after_scene_gate", ctx=deps.attempt_context):
        clear_stale_scene_classify_scratch(state)
        scene_cls = classify_scene(
            facts,
            bboxes,
            ocr_summary=ocr_merged,
            screen_w=sw,
            screen_h=sh,
            screenshot_path=shot_path,
        )
        scene_gate_judgment = None
        state["recover_hint"] = "classify:stale_abort"
        state["facts"] = facts.model_dump()
        state["session_work_generation"] = work_gen
        plan_route(state)
        logger.warning("[LaunchGraph:classify] stale_abort after scene_gate")
        return state  # type: ignore[return-value]
    if scene_gate_judgment is not None:
        logger.info(
            "[LaunchGraph:classify] scene_gate slug=%s strategy=%s (coords=label)",
            scene_gate_judgment.normalized_slug(),
            scene_gate_judgment.normalized_coord_strategy(),
        )
    scene_transition = detect_scene_transition(
        prev_scene_id=str(state.get("scene_id") or "unknown"),
        prev_fingerprint=str(state.get("scene_fingerprint") or ""),
        classification=scene_cls,
        facts=facts,
        ocr_summary=ocr_merged,
        screenshot_path=shot_path,
    )
    apply_scene_classification(state, scene_cls, scene_transition, facts)
    state["session_work_generation"] = capture_session_generation(deps.attempt_context)
    facts = facts_from_state(state)
    facts = merge_sticky_gate_facts(facts, prev_facts=prev_facts, state=state)
    if state.get("in_game_entry_passed") or scene_cls.scene_id == "in_game_hud":
        in_game_updates: dict = {}
        if facts.download_visible:
            in_game_updates.update(
                {
                    "download_visible": False,
                    "download_in_progress": False,
                    "download_action": "",
                    "download_gate_kind": "",
                    "download_progress_text": "",
                },
            )
        if in_game_updates:
            reason = facts.classify_reason
            if facts.download_visible and in_game_updates.get("download_visible") is False:
                reason = f"{reason}; in_game:download_suppressed" if reason else "in_game:download_suppressed"
            in_game_updates["classify_reason"] = reason[:500]
            facts = facts.model_copy(update=in_game_updates)
            state["facts"] = facts.model_dump()
    logger.info(
        "[LaunchGraph:classify] scene=%s conf=%.2f source=%s trans=%s active=%s",
        scene_cls.scene_id,
        scene_cls.confidence,
        scene_cls.source,
        scene_transition.kind,
        state.get("scene_strategy_active"),
    )
    if await _try_confirm_in_game_via_hud_ocr(
        state,
        deps,
        ocr_summary=ocr_merged,
        shot_path=shot,
        round_id=int(state.get("iteration") or 0),
        source="classify",
    ):
        plan_route(state)
        return state  # type: ignore[return-value]
    from game_agent.services.session_restart_recovery import maybe_complete_session_relogin_recovery

    maybe_complete_session_relogin_recovery(state, facts)
    plan_route(state)
    return state  # type: ignore[return-value]


def _infer_stage_label(facts, state: LaunchGraphState) -> str:
    if state.get("in_game_confirmed"):
        return "in_game"
    if state.get("session_relogin_recovery_active"):
        return "session_relogin"
    if state.get("session_agent_active") and not state.get("in_game_agent_done"):
        return "in_game_agent"
    if state.get("in_game_agent_started_at") and not state.get("in_game_agent_done"):
        return "in_game_agent"
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
    if facts.character_creation_blocking:
        return "character_creation"
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
        attempt_context=deps.attempt_context,
    )
    if result.verified:
        set_privacy_checked(state)
        deps.run_state.privacy_checkbox_tapped = True
        mark_tree_node_done(state, node, artifact=str(result.debug_marked_image or ""))
    else:
        note_action_failure(
            state,
            node=node,
            verify=NodeVerifyResult(passed=False, reason=result.message[:300]),
            ocr_before=get_last_ocr(state),
            ocr_after=get_last_ocr(state),
            expected_stage="privacy_checkbox",
        )
        mark_tree_node_failed(state, node, result.message)
    return state  # type: ignore[return-value]


async def handle_initial_privacy_dialog_node(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
) -> LaunchGraphState:
    state = dict(state)
    node = "handle_initial_privacy_dialog"
    facts = facts_from_state(state)
    if facts.agree_button_xy is None:
        mark_tree_node_failed(state, node, "no agree button coords")
        return state  # type: ignore[return-value]

    sw, sh = deps.screen_width, deps.screen_height
    ocr_before = get_last_ocr(state)

    async def _act(st: LaunchGraphState, attempt: int) -> str:
        from game_agent.services.privacy_gate import pick_consent_button_from_ocr

        f = facts_from_state(st)
        xy = f.agree_button_xy or facts.agree_button_xy
        if attempt > 1:
            bboxes = deserialize_bboxes(st.get("last_bboxes"))
            picked = pick_consent_button_from_ocr(bboxes) if bboxes else None
            if picked is not None:
                xy = (picked[0], picked[1])
                st["facts"] = f.model_copy(update={"agree_button_xy": xy}).model_dump()
        if xy is None:
            return "no agree coords"
        x, y = xy
        return deps.adb.tap(x, y, width=sw, height=sh)

    def _verify(st: LaunchGraphState, before: str, after: str):
        return verify_stage_exit(
            ocr_before=before,
            ocr_after=after,
            expected_stage="privacy_modal",
        )

    frame = await run_action_frame(
        state,
        node=node,
        adb=deps.adb,
        artifact_root=deps.artifact_root,
        screen_w=sw,
        screen_h=sh,
        act_fn=_act,
        verify_fn=_verify,
        max_attempts=3,
        ocr_before=ocr_before,
        expected_stage="privacy_modal",
        attempt_context=deps.attempt_context,
        foreground_poll_interval_s=_foreground_poll_interval(deps),
    )
    if frame.passed:
        mark_tree_node_done(state, node, artifact=frame.artifact, evidence=frame.evidence)
    else:
        reason = frame.last_reflection.reason if frame.last_reflection else "privacy verify failed"
        mark_tree_node_failed(state, node, reason[:500], artifact=frame.artifact)
    return state  # type: ignore[return-value]


async def handle_download_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    node = "handle_download"
    if state.get("in_game_entry_passed") or state.get("in_game_confirmed"):
        logger.info("[LaunchGraph:download] skipped — already in game")
        mark_tree_node_done(state, node, evidence="skipped_in_game")
        return state  # type: ignore[return-value]

    sw, sh = deps.screen_width, deps.screen_height
    facts = facts_from_state(state)
    ocr_before = get_last_ocr(state)

    async def _act(st: LaunchGraphState, attempt: int) -> str:
        from game_agent.services.download_gate import pick_continue_button_from_ocr

        f = facts_from_state(st)
        bboxes = deserialize_bboxes(st.get("last_bboxes"))
        if f.download_continue_xy is not None:
            x, y = f.download_continue_xy
            deps.adb.tap(x, y, width=sw, height=sh)
            return f"tapped continue ({x},{y})"
        picked = pick_continue_button_from_ocr(bboxes) if bboxes else None
        if picked is not None:
            deps.adb.tap(picked[0], picked[1], width=sw, height=sh)
            return f"tapped ocr continue ({picked[0]},{picked[1]})"
        deps.adb.wait_seconds(4.0)
        return "wait download"

    def _verify(st: LaunchGraphState, before: str, after: str):
        from game_agent.services.download_gate import ocr_still_downloading
        from game_agent.services.privacy_gate import ocr_has_privacy_context

        if ocr_has_privacy_context(after) and not ocr_still_downloading(after):
            return verify_stage_exit(
                ocr_before=before,
                ocr_after=after,
                expected_stage="privacy_modal",
            )
        if ocr_still_downloading(after):
            return verify_stage_exit(
                ocr_before=before,
                ocr_after=after,
                expected_stage="download",
            )
        return verify_stage_exit(
            ocr_before=before,
            ocr_after=after,
            expected_stage="download",
            completion_signals=["进入", "enter", "开始"],
        )

    frame = await run_action_frame(
        state,
        node=node,
        adb=deps.adb,
        artifact_root=deps.artifact_root,
        screen_w=sw,
        screen_h=sh,
        act_fn=_act,
        verify_fn=_verify,
        max_attempts=2,
        ocr_before=ocr_before,
        expected_stage="download",
        attempt_context=deps.attempt_context,
        foreground_poll_interval_s=_foreground_poll_interval(deps),
    )
    if frame.passed:
        from game_agent.services.download_gate import ocr_still_downloading

        ocr_after = state.get("last_ocr_summary") or ""
        if ocr_still_downloading(ocr_after):
            logger.info("[LaunchGraph:download] still in progress, not marking done")
            return state  # type: ignore[return-value]
        mark_tree_node_done(state, node, artifact=frame.artifact, evidence=frame.evidence)
    elif frame.last_reflection and frame.last_reflection.root_cause == "wrong_route":
        logger.info(
            "[LaunchGraph:download] wrong_route corrected, defer to privacy milestone"
        )
    else:
        reason = frame.last_reflection.reason if frame.last_reflection else "download verify failed"
        mark_tree_node_failed(state, node, reason[:500])
    return state  # type: ignore[return-value]


async def _refresh_overlay_capture(
    deps: LaunchGraphDeps,
    shot: Path,
    sw: int,
    sh: int,
    *,
    prefix: str,
) -> tuple[Path, list, str]:
    ts = datetime.now().strftime("%H%M%S_%f")
    new_shot = deps.artifact_root / f"{prefix}_{ts}.png"
    deps.adb.screencap_png(new_shot)
    ocr_summary, bboxes = await asyncio.to_thread(
        run_ocr_frame,
        new_shot,
        device_w=sw,
        device_h=sh,
        worker_key=deps.adb.device_serial,
    )
    return new_shot, bboxes, ocr_summary


async def dismiss_blocking_overlay_node(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
) -> LaunchGraphState:
    state = dict(state)
    node = "dismiss_blocking_overlay"
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

    recover_msg = recover_exit_confirm_dialog_if_present(
        deps.adb,
        bboxes,
        screen_w=sw,
        screen_h=sh,
    )
    if recover_msg:
        logger.info("[LaunchGraph:overlay] pre_act %s", recover_msg[:120])
        deps.adb.wait_seconds(0.5)
        shot, bboxes, ocr_before = await _refresh_overlay_capture(
            deps, shot, sw, sh, prefix="overlay_after_exit_cancel",
        )
        state["last_screenshot"] = str(shot.resolve())
        state["last_ocr_summary"] = ocr_before
        state["last_bboxes"] = serialize_bboxes(bboxes)

    last_dismiss_plan: list = [plan]

    async def _act(st: LaunchGraphState, attempt: int) -> str:
        dismiss_plan = await resolve_dismiss_target(
            llm_cfg=deps.app_config.llm_multimodal,
            screenshot_path=Path(st.get("last_screenshot") or shot),
            ocr_summary=st.get("last_ocr_summary") or ocr_before,
            bboxes=deserialize_bboxes(st.get("last_bboxes")) or bboxes,
            screen_w=sw,
            screen_h=sh,
            facts=facts_from_state(st),
            round_id=deps.round_id,
            attempt=attempt,
            skip_facts_xy=attempt > 1,
        )
        if dismiss_plan is None:
            return "no dismiss plan"
        last_dismiss_plan[0] = dismiss_plan
        tap_msg = deps.adb.tap(dismiss_plan.x, dismiss_plan.y, width=sw, height=sh)
        return tap_msg

    def _verify(st: LaunchGraphState, before: str, after: str):
        after_bboxes = deserialize_bboxes(st.get("last_bboxes"))
        recover_exit_confirm_dialog_if_present(
            deps.adb,
            after_bboxes,
            screen_w=sw,
            screen_h=sh,
        )
        overlay_verify = verify_overlay_dismissed(before, after)
        if overlay_verify.passed:
            return overlay_verify
        if not overlay_still_visible(after):
            return NodeVerifyResult(
                passed=True,
                reason="overlay no longer visible",
                evidence=overlay_verify.evidence,
            )
        return overlay_verify

    frame = await run_action_frame(
        state,
        node=node,
        adb=deps.adb,
        artifact_root=deps.artifact_root,
        screen_w=sw,
        screen_h=sh,
        act_fn=_act,
        verify_fn=_verify,
        max_attempts=2,
        ocr_before=ocr_before,
        expected_stage="announcement",
        attempt_context=deps.attempt_context,
        foreground_poll_interval_s=_foreground_poll_interval(deps),
    )
    if frame.passed:
        facts = facts.model_copy(
            update={
                "announcement_overlay": False,
                "announcement_dismiss_xy": None,
            },
        )
        state["facts"] = facts.model_dump()
        mark_tree_node_done(
            state,
            node,
            artifact=frame.artifact,
            evidence=f"{last_dismiss_plan[0].method}: {frame.evidence}",
        )
        logger.info(
            "[LaunchGraph:overlay] dismissed method=%s (%s,%s)",
            last_dismiss_plan[0].method,
            last_dismiss_plan[0].x,
            last_dismiss_plan[0].y,
        )
    else:
        after_bboxes = deserialize_bboxes(state.get("last_bboxes"))
        recover_msg = recover_exit_confirm_dialog_if_present(
            deps.adb,
            after_bboxes,
            screen_w=sw,
            screen_h=sh,
        )
        if recover_msg:
            logger.info("[LaunchGraph:overlay] post_fail %s", recover_msg[:120])
            deps.adb.wait_seconds(0.5)
        else:
            await asyncio.to_thread(
                dismiss_overlay,
                deps.adb.device_serial,
                sw,
                sh,
                allow_press_back=False,
            )
        reason = frame.last_reflection.reason if frame.last_reflection else "overlay still visible"
        mark_tree_node_failed(
            state,
            node,
            f"overlay still visible after {last_dismiss_plan[0].method}: {reason}",
            artifact=frame.artifact,
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

    if not result.ok or not result.ocr_verify_ok or not result.left_login_form:
        note_action_failure(
            state,
            node=node,
            verify=NodeVerifyResult(
                passed=False,
                reason=result.message[:300],
            ),
            ocr_before=get_last_ocr(state),
            ocr_after=result.verify_ocr_summary or "",
            expected_stage="login",
        )
        mark_tree_node_failed(state, node, result.message[:500])
        return state  # type: ignore[return-value]

    if result.verify_screenshot is None:
        mark_tree_node_failed(state, node, "login verify screenshot missing")
        return state  # type: ignore[return-value]

    if ocr_credential_login_passed(
        left_login_form=result.left_login_form,
        stage=result.stage,
    ):
        if cfg.llm_multimodal is not None:
            actx = deps.attempt_context
            entry_result = await run_in_game_check_on_capture(
                shot_path=result.verify_screenshot,
                ocr_summary=result.verify_ocr_summary,
                cfg=cfg,
                run_state=deps.run_state,
                audit=deps.audit,
                round_id=deps.round_id,
                sessions_restarted=actx.session_restarts if actx else 0,
                session_index=actx.get_session_index() if actx else 1,
                confirm_needed=1,
                provisional=True,
            )
            if entry_result.judgment is not None:
                store_game_entry_judgment(state, entry_result.judgment)
        set_login_done(state, evidence=result.message[:200])
        deps.run_state.launch_stage = "login_form"
        mark_tree_node_done(state, node, artifact=result.message[:200], evidence="atomic_login_ocr_ok")
        return state  # type: ignore[return-value]

    if cfg.llm_multimodal is None:
        mark_tree_node_failed(state, node, "llm_multimodal required for login VLM verify")
        return state  # type: ignore[return-value]

    actx = deps.attempt_context
    entry_result = await run_in_game_check_on_capture(
        shot_path=result.verify_screenshot,
        ocr_summary=result.verify_ocr_summary,
        cfg=cfg,
        run_state=deps.run_state,
        audit=deps.audit,
        round_id=deps.round_id,
        sessions_restarted=actx.session_restarts if actx else 0,
        session_index=actx.get_session_index() if actx else 1,
        confirm_needed=1,
        provisional=True,
    )
    if entry_result.judgment is not None:
        store_game_entry_judgment(state, entry_result.judgment)

    facts = facts_from_state(state)
    if not vlm_login_verify_passed(state, facts=facts):
        vlm_msg = entry_result.message[:300] if entry_result.message else "VLM login verify failed"
        note_action_failure(
            state,
            node=node,
            verify=NodeVerifyResult(passed=False, reason=vlm_msg),
            ocr_before=result.verify_ocr_summary or "",
            ocr_after=result.verify_ocr_summary or "",
            expected_stage="login",
        )
        mark_tree_node_failed(state, node, vlm_msg)
        return state  # type: ignore[return-value]

    set_login_done(state, evidence=result.message[:200])
    deps.run_state.launch_stage = "login_form"
    mark_tree_node_done(state, node, artifact=result.message[:200], evidence="atomic_login_ok")
    return state  # type: ignore[return-value]


async def select_sub_account_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    node = "select_sub_account"
    facts = facts_from_state(state)
    sw, sh = deps.screen_width, deps.screen_height

    cred, cred_err = _load_login_credentials(state, deps, node)
    if cred_err:
        mark_tree_node_failed(state, node, cred_err)
        return state  # type: ignore[return-value]

    target_phrases = resolve_sub_account_match_phrases(cred)
    target_display = sub_account_target_display(cred)
    bboxes = deserialize_bboxes(state.get("last_bboxes"))
    entry = pick_sub_account_bbox(bboxes, target_phrases=target_phrases, screen_w=sw)
    if entry is not None:
        facts = facts.model_copy(
            update={
                "sub_account_action_xy": (entry.cx, entry.cy),
                "sub_account_label": entry.text.strip(),
                "sub_account_blocking": True,
                "login_stage": "sub_account_select",
            },
        )
        state["facts"] = facts.model_dump()
    elif facts.sub_account_action_xy is None:
        mark_tree_node_failed(
            state,
            node,
            f"sub_account target not found: {target_display}",
        )
        return state  # type: ignore[return-value]

    ocr_before = get_last_ocr(state)
    signals = list(facts.screen_completion_signals)

    async def _act(st: LaunchGraphState, attempt: int) -> str:
        facts_now = facts_from_state(st)
        boxes = deserialize_bboxes(st.get("last_bboxes"))
        picked = pick_sub_account_bbox(boxes, target_phrases=target_phrases, screen_w=sw)
        if picked is not None:
            xy = (picked.cx, picked.cy)
            st["facts"] = facts_now.model_copy(
                update={
                    "sub_account_action_xy": xy,
                    "sub_account_label": picked.text.strip(),
                },
            ).model_dump()
        elif facts_now.sub_account_action_xy is not None:
            xy = facts_now.sub_account_action_xy
        else:
            return f"no sub-account target for {target_display}"
        return deps.adb.tap(xy[0], xy[1], width=sw, height=sh)

    def _verify(st: LaunchGraphState, before: str, after: str):
        return verify_stage_exit(
            ocr_before=before,
            ocr_after=after,
            expected_stage="sub_account_select",
            completion_signals=signals,
        )

    frame = await run_action_frame(
        state,
        node=node,
        adb=deps.adb,
        artifact_root=deps.artifact_root,
        screen_w=sw,
        screen_h=sh,
        act_fn=_act,
        verify_fn=_verify,
        max_attempts=3,
        ocr_before=ocr_before,
        expected_stage="sub_account_select",
        attempt_context=deps.attempt_context,
        foreground_poll_interval_s=_foreground_poll_interval(deps),
    )
    if frame.passed:
        clear_game_entry_judgment(state)
        set_sub_account_selected(state, evidence=frame.evidence)
        deps.run_state.launch_stage = "server_select"
        mark_tree_node_done(state, node, artifact=frame.artifact, evidence=frame.evidence)
    else:
        reason = frame.last_reflection.reason if frame.last_reflection else "sub-account verify failed"
        mark_tree_node_failed(state, node, reason[:500], artifact=frame.artifact, evidence=frame.evidence)
    return state  # type: ignore[return-value]


async def check_server_selector_node(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
) -> LaunchGraphState:
    state = dict(state)
    node = "check_server_selector"
    if not state.get("server_selector_check_enabled", True):
        set_server_checked(state, evidence="skipped:config")
        deps.run_state.server_checked = True
        deps.run_state.launch_stage = "server_select"
        mark_tree_node_done(state, node, evidence="skipped:config")
        logger.info("[LaunchGraph:server] check_server_selector skipped (config)")
        return state  # type: ignore[return-value]

    result, privacy_tapped = await run_full_server_selector_check_with_privacy_precheck(
        deps.adb,
        deps.artifact_root,
        deps.app_config,
        round_id=deps.round_id,
        privacy_checkbox_already_tapped=bool(state.get("privacy_checked")),
    )
    if privacy_tapped:
        set_privacy_checked(state)
        deps.run_state.privacy_checkbox_tapped = True
    deps.run_state.server_check_attempts += result.taps_used
    if result.ok:
        set_server_checked(state)
        deps.run_state.server_checked = True
        deps.run_state.launch_stage = "server_select"
        mark_tree_node_done(state, node)
    elif message_indicates_e2006(result.message):
        err = compact_failure_message(result.message)
        state["terminal_error"] = err
        state["finished"] = True
        deps.run_state.finished = True
        deps.run_state.success = False
        deps.run_state.note = err
        mark_tree_node_failed(state, node, result.message)
    else:
        mark_tree_node_failed(state, node, result.message)
    return state  # type: ignore[return-value]


async def tap_enter_game_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    state = dict(state)
    node = "tap_enter_game"
    facts = facts_from_state(state)
    sw, sh = deps.screen_width, deps.screen_height
    if not sw or not sh:
        sw, sh = deps.adb.touch_size()
        deps.screen_width, deps.screen_height = sw, sh

    ocr_before = get_last_ocr(state)
    bboxes = deserialize_bboxes(state.get("last_bboxes"))
    if not bboxes and not facts.enter_cta_visible:
        mark_tree_node_failed(state, node, "no enter gate visible")
        return state  # type: ignore[return-value]

    if not facts.enter_cta_visible and bboxes:
        if not enter_gate_likely_visible(bboxes, ocr_merged=ocr_before):
            mark_tree_node_failed(state, node, "no enter gate visible")
            return state  # type: ignore[return-value]

    stage_hint = "server_select"
    if completed_tree_node(state, "select_sub_account"):
        stage_hint = "sub_account_selected; tap primary enter-game CTA"
    elif facts.server_slot_visible:
        stage_hint = "server_select; optional server row visible; tap enter-game CTA"

    prior_failure = ""
    raw_reflection = state.get("last_reflection") or {}
    if isinstance(raw_reflection, dict):
        prior_failure = str(raw_reflection.get("reason") or "")

    tap_decision = await decide_enter_gate_tap(
        llm_cfg=deps.app_config.llm,
        bboxes=bboxes,
        ocr_summary=ocr_before,
        stage_hint=stage_hint,
        screen_w=sw,
        screen_h=sh,
        deepseek=deps.app_config.deepseek,
        prior_failure=prior_failure,
    )
    if tap_decision is None:
        mark_tree_node_failed(state, node, "main brain could not pick enter CTA")
        return state  # type: ignore[return-value]

    logger.info(
        "[tap_enter_game] brain_pick label=%r xy=(%d,%d) source=%s reason=%s",
        tap_decision.target_text[:60],
        tap_decision.x,
        tap_decision.y,
        tap_decision.source,
        tap_decision.reason[:120],
    )
    tap_xy = (tap_decision.x, tap_decision.y)

    async def _act(st: LaunchGraphState, attempt: int) -> str:
        x, y = tap_xy
        msg = deps.adb.tap(x, y, width=sw, height=sh)
        increment_enter_tapped(st)
        logger.info("[ActionFrame:%s] act: %s source=%s", node, msg[:120], tap_decision.source)
        return msg

    def _verify(st: LaunchGraphState, before: str, after: str):
        return verify_stage_exit(
            ocr_before=before,
            ocr_after=after,
            expected_stage="server_select",
            completion_signals=facts.screen_completion_signals,
        )

    frame = await run_action_frame(
        state,
        node=node,
        adb=deps.adb,
        artifact_root=deps.artifact_root,
        screen_w=sw,
        screen_h=sh,
        act_fn=_act,
        verify_fn=_verify,
        max_attempts=2,
        ocr_before=ocr_before,
        expected_stage="server_select",
        attempt_context=deps.attempt_context,
        foreground_poll_interval_s=_foreground_poll_interval(deps),
    )
    if frame.passed:
        from game_agent.services.session_agent import activate_session_agent

        activate_session_agent(state, reason="tap_enter_game")
        mark_tree_node_done(state, node, artifact=frame.artifact, evidence=frame.evidence)
    else:
        reason = frame.last_reflection.reason if frame.last_reflection else "enter tap verify failed"
        mark_tree_node_failed(state, node, reason[:500], artifact=frame.artifact)
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
        provisional=True,
        attempt_context=actx,
    )
    if result.confirmed:
        state["in_game_entry_passed"] = True
        if result.judgment is not None:
            store_game_entry_judgment(state, result.judgment)
        mark_tree_node_done(state, node, evidence=result.message[:500])
        logger.info("[LaunchGraph:check_in_game] provisional entry passed, route stability_observe")
    elif result.judgment is not None and (
        result.ocr_creation_hits
        or result.judgment.stage == "character_creation"
        or "character_creation" in (result.judgment.blockers or [])
    ):
        from game_agent.models.launch_graph_state import facts_from_state

        facts = facts_from_state(state)
        state["facts"] = facts.model_copy(
            update={"character_creation_blocking": True},
        ).model_dump()
        mark_tree_node_done(
            state,
            node,
            evidence=(result.message or "character_creation, continue flow")[:500],
        )
        logger.info(
            "[LaunchGraph:check_in_game] not in game (character_creation), route adaptive/dynamic",
        )
    else:
        mark_tree_node_failed(state, node, result.message[:500])
    return state  # type: ignore[return-value]


async def stability_observe_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    """进游戏后短期稳定性观察；通过后启动 in-game agent（不再立即终局）。"""
    state = dict(state)
    node = "stability_observe"
    actx = deps.attempt_context
    cfg = deps.app_config
    observe_s = float(cfg.game.stability_observe_s)
    interval_s = float(cfg.game.stability_check_interval_s)

    stability_rounds = int(state.get("stability_rounds") or 0) + 1
    state["stability_rounds"] = stability_rounds
    limits = launch_graph_limits_from_state(state)
    if stability_rounds > limits.max_stability_observe_rounds:
        err = f"stability max rounds ({limits.max_stability_observe_rounds})"
        mark_tree_node_failed(state, node, err)
        state["finished"] = True
        state["terminal_error"] = err
        deps.run_state.finished = True
        deps.run_state.success = False
        deps.run_state.note = err
        return state  # type: ignore[return-value]

    now = time.monotonic()
    started = float(state.get("stability_observe_started_at") or 0.0)
    if started <= 0.0:
        state["stability_observe_started_at"] = now
        state["stability_observe_deadline"] = now + observe_s
        started = now
        logger.info(
            "[LaunchGraph:stability] observe_start duration=%.0fs interval=%.0fs",
            observe_s,
            interval_s,
        )

    deadline = float(state.get("stability_observe_deadline") or (started + observe_s))
    last_check = float(state.get("stability_last_check_at") or 0.0)
    at_deadline = now >= deadline
    should_check = at_deadline or last_check <= 0.0 or (now - last_check) >= interval_s

    if not should_check:
        wait_s = min(interval_s, max(0.5, deadline - now))
        deps.adb.wait_seconds(wait_s)
        mark_tree_node_done(state, node, evidence="stability_wait")
        return state  # type: ignore[return-value]

    result = await run_stability_check(
        adb=deps.adb,
        cfg=cfg,
        artifact_root=deps.artifact_root,
        round_id=stability_rounds,
        audit=deps.audit,
        attempt_context=deps.attempt_context,
    )
    state["stability_last_check_at"] = time.monotonic()
    state["last_screenshot"] = str(result.screenshot_path.resolve())

    if result.has_fatal_anomaly:
        err = (result.reason or "stability fatal anomaly")[:2000]
        mark_tree_node_failed(
            state,
            node,
            err[:500],
            artifact=str(result.screenshot_path.resolve()),
        )
        state["finished"] = True
        state["terminal_error"] = err
        deps.run_state.finished = True
        deps.run_state.success = False
        deps.run_state.note = err
        state["recover_hint"] = f"stability_failed:{err[:200]}"
        logger.warning("[LaunchGraph:stability] fatal anomaly | %s", err[:200])
        return state  # type: ignore[return-value]

    if at_deadline:
        note = (result.reason or "In-game stability confirmed, starting agent")[:2000]
        _start_in_game_play_session(state, deps)
        mark_tree_node_done(
            state,
            node,
            artifact=str(result.screenshot_path.resolve()),
            evidence=note[:500],
        )
        logger.info(
            "[LaunchGraph:stability] observe_pass rounds=%d → in_game_agent | %s",
            stability_rounds,
            note[:200],
        )
        return state  # type: ignore[return-value]

    mark_tree_node_done(
        state,
        node,
        artifact=str(result.screenshot_path.resolve()),
        evidence=result.reason[:200],
    )
    return state  # type: ignore[return-value]


def _start_in_game_play_session(state: LaunchGraphState, deps: LaunchGraphDeps) -> None:
    cfg = deps.app_config
    now = time.monotonic()
    state["stability_observe_complete"] = True
    state["in_game_play_started_at"] = now
    state["in_game_agent_started_at"] = now
    state["current_stage"] = "in_game_agent"
    state["in_game_vlm_no_progress_streak"] = 0
    state["last_in_game_progress_analysis"] = {}
    deps.run_state.launch_stage = "in_game_play"
    actx = deps.attempt_context
    audit = deps.audit
    if actx is not None:
        actx.signal_in_game_play_started()
        actx.set_ui_observation("in_game_play")
    if audit is not None:
        audit.log_phase(
            "in_game_play",
            "in_game_play_started",
            mode=cfg.game.in_game_mode,
            goal="brain_tutorial_complete",
        )


def _finish_in_game_agent_success(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
    *,
    note: str,
    artifact: str = "",
) -> None:
    actx = deps.attempt_context
    audit = deps.audit
    cfg = deps.app_config
    started = float(state.get("in_game_agent_started_at") or 0.0)
    elapsed_s = max(0.0, time.monotonic() - started) if started > 0 else 0.0
    state["in_game_play_completed"] = True
    state["in_game_play_rounds"] = int(
        state.get("in_game_play_rounds") or state.get("in_game_agent_rounds") or 0
    )
    state["in_game_play_duration_s"] = int(elapsed_s)
    state["in_game_mode"] = cfg.game.in_game_mode
    set_in_game_confirmed(state, evidence=note[:500])
    state["in_game_agent_done"] = True
    state["finished"] = True
    state["current_stage"] = "in_game"
    deps.run_state.in_game_confirmed = True
    deps.run_state.finished = True
    deps.run_state.success = True
    deps.run_state.note = note
    deps.run_state.launch_stage = "in_game"
    if actx is not None:
        actx.signal_in_game_confirmed(note)
    if audit is not None:
        audit.log_phase(
            "in_game_play",
            "brain_success",
            duration_s=int(elapsed_s),
            rounds=int(state.get("in_game_play_rounds") or 0),
            replan_count=int(state.get("in_game_behavior_replan_count") or 0),
            chains_built=int(state.get("in_game_play_chains_built") or 0),
            steps_executed=int(state.get("in_game_play_steps_executed") or 0),
            streak=int(state.get("in_game_brain_success_streak") or 0),
        )
    mark_tree_node_done(
        state,
        "in_game_agent",
        artifact=artifact,
        evidence=note[:500],
    )


def _finish_in_game_agent_brain_fail(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
    *,
    note: str,
) -> None:
    """局内主脑判定卡住且无安全下一步。"""
    node = "in_game_agent"
    err = note[:2000]
    mark_tree_node_failed(state, node, err)
    state["in_game_play_failed_reason"] = err
    state["finished"] = True
    state["terminal_error"] = err
    deps.run_state.finished = True
    deps.run_state.success = False
    deps.run_state.note = err
    actx = deps.attempt_context
    if actx is not None:
        actx.clear_in_game_play_session()
    if deps.audit is not None:
        deps.audit.log_phase(
            "in_game_play",
            "brain_fail",
            rounds=int(state.get("in_game_agent_rounds") or 0),
            fail_streak=int(state.get("in_game_brain_fail_streak") or 0),
        )
    logger.warning("[LaunchGraph:in_game_agent] brain fail | %s", err[:200])


async def in_game_agent_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    """进入游戏后：VLM 画面分析 + 主脑终局决策，推进新手引导直至 success/fail。"""
    state = dict(state)
    node = "in_game_agent"
    actx = deps.attempt_context
    if state.get("session_relogin_recovery_active"):
        mark_tree_node_done(state, node, evidence="session_relogin_recovery_active")
        state["recover_hint"] = "deferred:in_game_agent:session_relogin_recovery"
        return state  # type: ignore[return-value]
    if actx is not None and actx.is_session_invalidated():
        if actx.consume_session_relogin_recovery():
            from game_agent.services.session_restart_recovery import apply_session_restart_to_state

            apply_session_restart_to_state(
                state,
                evidence="in_game_agent:session_invalidated",
                session_index=actx.get_session_index(),
            )
            actx.acknowledge_session_invalidation()
        mark_tree_node_done(state, node, evidence="session_invalidated_mid_node")
        state["recover_hint"] = "in_game_agent:session_invalidated"
        return state  # type: ignore[return-value]
    cfg = deps.app_config
    limits = launch_graph_limits_from_state(state)

    now = time.monotonic()
    started = float(state.get("in_game_agent_started_at") or 0.0)
    if started <= 0.0:
        _start_in_game_play_session(state, deps)
        started = float(state.get("in_game_agent_started_at") or now)

    interval_s = float(cfg.game.in_game_agent_interval_s)
    last_action_at = float(state.get("in_game_agent_last_action_at") or 0.0)
    if last_action_at > 0 and (now - last_action_at) < interval_s:
        wait_s = interval_s - (now - last_action_at)
        deps.adb.wait_seconds(max(0.5, wait_s))
        mark_tree_node_done(state, node, evidence="in_game_agent_interval_wait")
        return state  # type: ignore[return-value]

    agent_rounds = int(state.get("in_game_agent_rounds") or 0) + 1
    state["in_game_agent_rounds"] = agent_rounds
    state["in_game_play_rounds"] = agent_rounds
    elapsed_s = max(0.0, now - started)

    sw, sh = deps.screen_width, deps.screen_height
    if not sw or not sh:
        sw, sh = deps.adb.touch_size()
        deps.screen_width, deps.screen_height = sw, sh

    if actx is not None:
        actx.set_ocr_busy(True)
    motion_summary = ""
    spatial_hints = ""
    motion_annotated_path: Path | None = None
    motion_result = None
    try:
        from game_agent.services.motion_burst import capture_motion_burst
        from game_agent.services.motion_probe import run_motion_probe
        from game_agent.services.motion_probe_policy import (
            motion_probe_lifecycle_allowed,
            should_run_motion_burst,
        )
        from game_agent.services.spatial_fuse import spatial_fuse

        motion_cfg = cfg.game.motion_probe
        lifecycle_ok = motion_probe_lifecycle_allowed(state, cfg=cfg.game)
        run_burst = lifecycle_ok and should_run_motion_burst(state, cfg=cfg.game)
        state["last_motion_probe_enabled"] = run_burst

        ts = datetime.now().strftime("%H%M%S_%f")
        if run_burst:
            burst = capture_motion_burst(
                deps.adb,
                deps.artifact_root,
                prefix=f"graph_in_game_agent_{agent_rounds:03d}",
                motion_cfg=motion_cfg,
            )
            shot = burst.keyframe_path
            state["motion_probe_rounds"] = int(state.get("motion_probe_rounds") or 0) + 1
            motion_task = asyncio.to_thread(
                run_motion_probe,
                burst.frame_paths,
                artifact_root=deps.artifact_root,
                round_id=agent_rounds,
                motion_cfg=motion_cfg,
            )
            ocr_task = asyncio.to_thread(
                run_ocr_frame,
                shot,
                device_w=sw,
                device_h=sh,
                worker_key=deps.adb.device_serial,
            )
            motion_result, (ocr_summary, bboxes) = await asyncio.gather(motion_task, ocr_task)
            fuse = spatial_fuse(bboxes, motion_result, motion_cfg=motion_cfg)
            motion_summary = motion_result.summary_text
            spatial_hints = fuse.hints_text
            motion_annotated_path = motion_result.annotated_path
            state["last_motion_summary"] = motion_summary
            state["last_spatial_hints"] = spatial_hints
            if deps.audit is not None:
                deps.audit.log_observer(
                    kind="motion_probe",
                    message=motion_summary[:500],
                    round_id=agent_rounds,
                    extra={
                        "burst_frames": len(burst.frame_paths),
                        "pulse_regions": sum(
                            1 for r in motion_result.regions if r.kind == "pulsing_fixed"
                        ),
                        "motion_regions": sum(
                            1 for r in motion_result.regions if r.kind == "moving_sprite"
                        ),
                        "top_tap": list(fuse.top_tap_candidate or ()),
                        "top_tap_score": fuse.top_tap_score,
                        "top_tap_reason": fuse.top_tap_reason,
                        "heatmap": str(motion_result.heatmap_path or ""),
                        "annotated": str(motion_result.annotated_path or ""),
                    },
                )
        else:
            shot = deps.artifact_root / f"graph_in_game_agent_{agent_rounds:03d}_{ts}.png"
            deps.adb.screencap_png(shot)
            ocr_summary, bboxes = await asyncio.to_thread(
                run_ocr_frame,
                shot,
                device_w=sw,
                device_h=sh,
                worker_key=deps.adb.device_serial,
            )
            state["last_motion_summary"] = ""
            state["last_spatial_hints"] = ""
    finally:
        if actx is not None:
            actx.set_ocr_busy(False)

    state["last_screenshot"] = str(shot.resolve())
    state["last_ocr_summary"] = ocr_summary
    state["last_bboxes"] = serialize_bboxes(bboxes)

    shot_hash = ""
    try:
        shot_hash = hashlib.sha256(shot.read_bytes()).hexdigest()[:16]
    except OSError:
        pass

    from game_agent.services.scene_label_runner import try_scene_label_fast_path

    label_result = await try_scene_label_fast_path(
        state,
        shot=shot,
        ocr_summary=ocr_summary,
        bboxes=bboxes,
        round_id=agent_rounds,
        sw=sw,
        sh=sh,
        artifact_root=deps.artifact_root,
        adb=deps.adb,
        actx=actx,
        audit=deps.audit,
        scope="in_game",
        node=node,
        cfg=cfg.game.scene_labels,
        motion_result=motion_result,
    )
    if label_result.handled and label_result.success:
        return state  # type: ignore[return-value]

    from game_agent.services.in_game_heuristic_fast_path import try_in_game_heuristic_fast_path

    heuristic_result = await try_in_game_heuristic_fast_path(
        state,
        shot=shot,
        ocr_summary=ocr_summary,
        bboxes=bboxes,
        agent_rounds=agent_rounds,
        sw=sw,
        sh=sh,
        artifact_root=deps.artifact_root,
        adb=deps.adb,
        actx=actx,
        audit=deps.audit,
        node=node,
        fast_path_enabled=bool(cfg.game.in_game_fast_path_enabled),
    )
    if heuristic_result.handled and heuristic_result.success:
        return state  # type: ignore[return-value]

    analyze_result = await run_in_game_screen_analyze_on_capture(
        shot_path=shot,
        ocr_summary=ocr_summary,
        cfg=cfg,
        state=state,
        round_id=agent_rounds,
        audit=deps.audit,
        motion_summary=motion_summary,
        spatial_hints=spatial_hints,
        annotated_path=motion_annotated_path,
        bboxes=bboxes,
        motion_result=motion_result,
        screen_w=sw,
        screen_h=sh,
        shot_hash=shot_hash,
        attempt_context=deps.attempt_context,
    )
    screen_analysis = analyze_result.analysis
    from game_agent.modules.session_invalidation import guard_session_work

    if guard_session_work(state, ctx=deps.attempt_context, where="in_game_agent:post_analyze"):
        mark_tree_node_done(state, node, evidence="stale_session_discard")
        state["recover_hint"] = "in_game_agent:stale_session_discard"
        return state  # type: ignore[return-value]

    prior_sig = str(state.get("in_game_agent_last_action_signature") or "")
    same_streak = int(state.get("in_game_agent_same_action_streak") or 0)
    external_summary = str(state.get("external_log_summary") or state.get("gameturbo_summary") or "")
    prior_verdict = str(state.get("in_game_brain_last_verdict") or "")
    active_step = get_current_in_game_behavior_step(state)
    active_chain = get_in_game_behavior_chain(state) if active_step else None

    decision = await decide_in_game_session_round(
        llm_cfg=cfg.llm,
        deepseek=cfg.deepseek,
        bboxes=bboxes,
        ocr_summary=ocr_summary,
        screen_analysis=screen_analysis,
        motion_summary=motion_summary,
        spatial_hints=spatial_hints,
        round_id=agent_rounds,
        elapsed_s=elapsed_s,
        external_log_summary=external_summary,
        failure_trace=state.get("in_game_behavior_failure_trace") or [],
        replan_from_step_id=str(state.get("in_game_behavior_last_failed_step_id") or ""),
        same_action_streak=same_streak,
        behavior_no_progress=int(state.get("in_game_behavior_no_progress") or 0),
        vlm_no_progress_streak=int(state.get("in_game_vlm_no_progress_streak") or 0),
        vlm_no_progress_fail_rounds=int(cfg.game.in_game_vlm_no_progress_fail_rounds),
        prior_verdict=prior_verdict,
        prior_action_signature=prior_sig,
        active_chain_goal=active_chain.goal if active_chain else "",
        active_step_intent=(active_step.intent if active_step else ""),
        screen_w=sw,
        screen_h=sh,
        max_action_wait_s=float(cfg.game.in_game_agent_max_action_wait_s),
    )

    brain_result = apply_brain_decision_to_state(
        state,
        decision,
        cfg=cfg,
        audit=deps.audit,
        round_id=agent_rounds,
    )

    if brain_result.success_confirmed:
        note = f"Tutorial complete (brain streak={brain_result.success_streak}): {decision.reason}"
        if decision.analysis:
            note = f"{note} | {decision.analysis[:300]}"
        _finish_in_game_agent_success(
            state,
            deps,
            note=note,
            artifact=str(shot.resolve()),
        )
        logger.info("[LaunchGraph:in_game_agent] brain success | %s", note[:200])
        return state  # type: ignore[return-value]

    if brain_result.fail_confirmed:
        note = f"In-game stuck (brain streak={brain_result.fail_streak}): {decision.reason}"
        if decision.analysis:
            note = f"{note} | {decision.analysis[:300]}"
        _finish_in_game_agent_brain_fail(state, deps, note=note)
        return state  # type: ignore[return-value]

    if decision.verdict == "success" and not brain_result.success_confirmed:
        mark_tree_node_done(
            state,
            node,
            evidence=f"brain_success_pending:{decision.reason[:80]}",
        )
        return state  # type: ignore[return-value]

    if decision.verdict == "wait":
        wait_s = max(0.5, min(decision.wait_s, float(cfg.game.in_game_agent_max_action_wait_s)))
        deps.adb.wait_seconds(wait_s)
        state["in_game_agent_last_action_at"] = time.monotonic()
        mark_tree_node_done(state, node, evidence=f"brain_wait:{decision.reason[:80]}")
        return state  # type: ignore[return-value]

    if decision.verdict == "continue":
        if decision.retry_strategy == "replan":
            clear_in_game_behavior_chain(state, completed=False)
        if decision.behavior_chain is not None:
            set_in_game_behavior_chain(state, decision.behavior_chain)
            state["in_game_play_chains_built"] = int(state.get("in_game_play_chains_built") or 0) + 1
            if deps.audit is not None:
                deps.audit.log_phase(
                    "in_game_play",
                    "behavior_chain_built",
                    steps=len(decision.behavior_chain.steps),
                    source=decision.source,
                    goal=decision.behavior_chain.goal[:200],
                )

        dim_cfg = cfg.game.dialogue_dim_tap
        ui_stage = screen_analysis.ui_stage if screen_analysis else ""
        from game_agent.services.technique_selection_heuristics import should_allow_dim_region_in_game

        if should_allow_dim_region_in_game(
            ui_stage=ui_stage,
            ocr_summary=ocr_summary,
            screen_analysis=screen_analysis,
            state=state,
        ):
            from game_agent.services.behavior_chain import apply_dim_region_to_chain
            from game_agent.services.dialogue_advance_state import should_use_dim_region_tap
            from game_agent.services.dialogue_dim_locator import locate_dialogue_dim_tap

            if should_use_dim_region_tap(state, cfg=dim_cfg, screen_analysis=screen_analysis):
                dim_xy = locate_dialogue_dim_tap(
                    shot,
                    bboxes=bboxes,
                    screen_w=sw,
                    screen_h=sh,
                    cfg=dim_cfg,
                    artifact_root=deps.artifact_root,
                    annotate_name=f"dialogue_dim_ingame_{agent_rounds:03d}.png",
                )
                if dim_xy is not None:
                    chain = get_in_game_behavior_chain(state)
                    hint = (
                        screen_analysis.dim_region_hint
                        if screen_analysis and screen_analysis.dim_region_hint
                        else "dim_region dialogue advance"
                    )
                    updated = apply_dim_region_to_chain(chain, dim_xy, reason=hint)
                    if updated is not None and updated.source == "dim_region":
                        set_in_game_behavior_chain(state, updated)

    behavior_step = get_current_in_game_behavior_step(state)
    if behavior_step is not None:
        before_fp = str(state.get("in_game_behavior_last_fingerprint") or "")
        before_ocr = ocr_summary
        scene_hint = screen_analysis.ui_stage if screen_analysis else ""
        behavior_step = sanitize_press_back_step(behavior_step, ocr_summary=before_ocr)
        behavior_step = sanitize_dialogue_blank_tap(
            behavior_step,
            ocr_summary=before_ocr,
            bboxes=bboxes,
            screen_w=sw,
            screen_h=sh,
        )
        from game_agent.services.tap_coord_resolver import resolve_step_coordinates

        behavior_step = resolve_step_coordinates(
            behavior_step,
            screen_analysis=screen_analysis,
            bboxes=bboxes,
            motion_result=motion_result,
            screen_w=sw,
            screen_h=sh,
            ocr_summary=before_ocr,
            shot_path=shot,
        )
        if guard_session_work(state, ctx=deps.attempt_context, where="in_game_agent:pre_tap"):
            mark_tree_node_done(state, node, evidence="stale_session_discard")
            state["recover_hint"] = "in_game_agent:stale_session_discard"
            return state  # type: ignore[return-value]
        step_started = time.monotonic()
        exec_msg = execute_behavior_step(behavior_step, adb=deps.adb, sw=sw, sh=sh)
        if behavior_step.action not in ("wait", "none"):
            deps.adb.wait_seconds(0.6)
        state["in_game_agent_last_action_at"] = time.monotonic()

        ts = datetime.now().strftime("%H%M%S_%f")
        after_shot = deps.artifact_root / f"graph_in_game_behavior_{agent_rounds:03d}_{ts}.png"
        deps.adb.screencap_png(after_shot)
        if actx is not None:
            actx.set_ocr_busy(True)
        try:
            after_ocr, after_bboxes = await asyncio.to_thread(
                run_ocr_frame,
                after_shot,
                device_w=sw,
                device_h=sh,
                worker_key=deps.adb.device_serial,
            )
        finally:
            if actx is not None:
                actx.set_ocr_busy(False)

        state["last_screenshot"] = str(after_shot.resolve())
        state["last_ocr_summary"] = after_ocr
        state["last_bboxes"] = serialize_bboxes(after_bboxes)
        after_fp = behavior_progress_fingerprint(
            ocr_summary=after_ocr,
            stage=behavior_step.intent or behavior_step.label,
            bboxes=after_bboxes,
            screen_h=sh,
            scene_hint=scene_hint,
        )
        progressed = not before_fp or after_fp != before_fp or ocr_progressed(before_ocr, after_ocr)
        criteria_ok, criteria_reason = evaluate_step_success(
            behavior_step,
            before_ocr=before_ocr,
            after_ocr=after_ocr,
        )
        action_ok = "refused" not in str(exec_msg).lower()
        if behavior_step.success_criteria:
            step_passed = criteria_ok and action_ok
        else:
            step_passed = progressed and action_ok
        if press_back_caused_exit_confirm(before_ocr=before_ocr, after_ocr=after_ocr):
            step_passed = False
            criteria_reason = "press_back_exit_confirm"
        from game_agent.services.technique_selection_heuristics import is_technique_selection_screen

        technique_before = is_technique_selection_screen(
            before_ocr, screen_analysis=screen_analysis
        )
        technique_after = is_technique_selection_screen(after_ocr)
        if behavior_step.id == "dim_region_tap" and (technique_before or technique_after):
            step_passed = False
            criteria_reason = "dim_tap_on_technique_selection"
        if scene_hint == "dialog" and not technique_before and not technique_after:
            from game_agent.services.dialogue_advance_state import (
                record_dialogue_advance_progress,
                set_dialogue_dim_last_tap,
            )

            used_dim = behavior_step.id == "dim_region_tap"
            record_dialogue_advance_progress(
                state,
                progressed=step_passed,
                used_dim_tap=used_dim and step_passed,
            )
            if used_dim and step_passed and behavior_step.x > 0 and behavior_step.y > 0:
                set_dialogue_dim_last_tap(state, behavior_step.x, behavior_step.y)
        if step_passed:
            completed_step = mark_in_game_behavior_attempt(state, behavior_step, done=True)
            state["in_game_behavior_no_progress"] = 0
            state["in_game_play_steps_executed"] = int(state.get("in_game_play_steps_executed") or 0) + 1
            advance_in_game_behavior_cursor(state)
            if deps.audit is not None:
                deps.audit.log_phase(
                    "in_game_play",
                    "behavior_step_done",
                    step_id=completed_step.id,
                    intent=completed_step.intent[:120],
                    duration_ms=int((time.monotonic() - step_started) * 1000),
                )
            logger.info(
                "[LaunchGraph:in_game_behavior] step_done id=%s action=%s intent=%s | %s",
                completed_step.id,
                completed_step.action,
                completed_step.intent[:100],
                str(exec_msg)[:120],
            )
            from game_agent.services.scene_memory_runner import learn_scene_memory_after_step

            learn_scene_memory_after_step(
                state,
                artifact_root=deps.artifact_root,
                before_ocr=before_ocr,
                after_ocr=after_ocr,
                bboxes=bboxes,
                step=completed_step,
                round_id=agent_rounds,
                screenshot_ref=str(after_shot.resolve()),
                screen_w=sw,
                screen_h=sh,
                screen_analysis=screen_analysis,
                step_passed=True,
            )
        else:
            attempted_step = mark_in_game_behavior_attempt(state, behavior_step, done=False)
            no_progress = int(state.get("in_game_behavior_no_progress") or 0) + 1
            state["in_game_behavior_no_progress"] = no_progress
            step_exhausted = attempted_step.attempts >= limits.max_dynamic_step_attempts
            if step_exhausted or no_progress >= limits.max_dynamic_no_progress:
                reason = (
                    f"in_game behavior step {attempted_step.id} stalled "
                    f"attempts={attempted_step.attempts} no_progress={no_progress}"
                )
                record_in_game_behavior_failure(
                    state,
                    attempted_step,
                    reason=reason,
                    ocr_summary=after_ocr,
                    artifact=str(after_shot.resolve()),
                )
                if deps.audit is not None:
                    deps.audit.log_phase(
                        "in_game_play",
                        "behavior_step_failed",
                        step_id=attempted_step.id,
                        intent=attempted_step.intent[:120],
                        reason=reason[:200],
                        duration_ms=int((time.monotonic() - step_started) * 1000),
                    )
                clear_in_game_behavior_chain(state, completed=False)
                logger.warning(
                    "[LaunchGraph:in_game_behavior] step_failed id=%s, brain decides next round",
                    attempted_step.id,
                )
            else:
                logger.info(
                    "[LaunchGraph:in_game_behavior] step_retry id=%s progress=%s criteria=%s | %s",
                    attempted_step.id,
                    progressed,
                    criteria_reason,
                    str(exec_msg)[:120],
                )
        if behavior_step.action not in ("wait", "none"):
            from game_agent.services.in_game_progress_judge import update_streak_after_action

            streak, force_fail, progress_reason = await update_streak_after_action(
                state,
                cfg=cfg,
                before=screen_analysis,
                after_shot=after_shot,
                before_ocr=before_ocr,
                after_ocr=after_ocr,
                action=behavior_step.action,
                round_id=agent_rounds,
                audit=deps.audit,
            )
            if force_fail:
                note = f"VLM no progress after {streak} action rounds: {progress_reason}"
                _finish_in_game_agent_brain_fail(state, deps, note=note)
                return state  # type: ignore[return-value]
        state["in_game_behavior_last_fingerprint"] = after_fp
        sig = behavior_step.signature()
        if sig == prior_sig:
            same_streak += 1
        else:
            same_streak = 1
        state["in_game_agent_same_action_streak"] = same_streak
        state["in_game_agent_last_action_signature"] = sig
        mark_tree_node_done(
            state,
            node,
            artifact=str(after_shot.resolve()),
            evidence=f"behavior:{behavior_step.action}:{behavior_step.intent[:120]}",
        )
        state["recover_hint"] = f"in_game_behavior:{behavior_step.intent[:200]}"
        return state  # type: ignore[return-value]

    if decision.verdict == "continue" and behavior_step is None and screen_analysis is not None:
        from game_agent.services.in_game_progress_judge import (
            apply_vlm_no_progress_streak,
            load_progress_analysis,
            store_progress_analysis,
            vlm_session_progressed,
        )

        prior_progress = load_progress_analysis(state)
        if prior_progress is not None:
            progressed = vlm_session_progressed(
                prior_progress,
                screen_analysis,
                min_confidence=float(cfg.game.in_game_vlm_progress_min_confidence),
            )
            streak, force_fail = apply_vlm_no_progress_streak(
                state,
                progressed,
                fail_threshold=int(cfg.game.in_game_vlm_no_progress_fail_rounds),
            )
            if deps.audit is not None and progressed is not None:
                deps.audit.log_observer(
                    kind="in_game_vlm_progress",
                    message=f"no_action_round progressed={progressed}",
                    round_id=agent_rounds,
                    extra={"streak": streak, "source": "round_compare"},
                )
            if force_fail:
                note = f"VLM no progress after {streak} rounds (no executable step)"
                _finish_in_game_agent_brain_fail(state, deps, note=note)
                return state  # type: ignore[return-value]
        store_progress_analysis(state, screen_analysis)

    mark_tree_node_done(
        state,
        node,
        artifact=str(shot.resolve()),
        evidence=f"brain:{decision.verdict}:{decision.reason[:120]}",
    )
    state["recover_hint"] = f"in_game_brain:{decision.reason[:200]}"
    logger.info(
        "[LaunchGraph:in_game_agent] round=%d elapsed=%.0fs verdict=%s | %s",
        agent_rounds,
        elapsed_s,
        decision.verdict,
        decision.reason[:120],
    )
    return state  # type: ignore[return-value]


async def adaptive_phase_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    """登录后可变 UI：PhaseEngine 模板单轮（plan → act → verify → commit）。"""
    return await run_adaptive_phase_once(state, deps)  # type: ignore[return-value]


def _execute_free_action(
    plan: FreeActionPlan,
    *,
    adb,
    sw: int,
    sh: int,
) -> str:
    if plan.action == "tap_xy" or plan.action == "tap_text":
        if plan.x <= 0 or plan.y <= 0:
            return f"refused tap invalid ({plan.x},{plan.y})"
        return adb.tap(plan.x, plan.y, width=sw, height=sh)
    if plan.action == "press_back":
        return adb.press_back()
    if plan.action == "wait":
        return adb.wait_seconds(plan.wait_s)
    return "no-op"


def _execute_dynamic_step(
    step: DynamicActionStep,
    *,
    adb,
    sw: int,
    sh: int,
) -> str:
    return execute_behavior_step(step, adb=adb, sw=sw, sh=sh)


async def dynamic_action_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    """执行动态子树链当前步骤（attempt 内有序链表）。"""
    state = dict(state)
    node = "dynamic_action"
    limits = launch_graph_limits_from_state(state)
    step = get_current_step(state)
    if step is None:
        mark_tree_node_failed(state, node, "no active dynamic step")
        clear_dynamic_chain(state, failed=True)
        return state  # type: ignore[return-value]

    dynamic_rounds = int(state.get("dynamic_rounds") or 0) + 1
    state["dynamic_rounds"] = dynamic_rounds
    if dynamic_rounds > limits.max_dynamic_rounds:
        mark_tree_node_failed(state, node, f"dynamic max rounds ({limits.max_dynamic_rounds})")
        clear_dynamic_chain(state, failed=True)
        logger.warning("[LaunchGraph:dynamic] dynamic_exit max_rounds=%d", limits.max_dynamic_rounds)
        return state  # type: ignore[return-value]

    no_progress = int(state.get("dynamic_no_progress") or 0)
    if no_progress >= limits.max_dynamic_no_progress:
        reason = f"dynamic no progress ({limits.max_dynamic_no_progress})"
        will_replan = can_replan_dynamic_chain(
            state,
            max_replans=limits.max_dynamic_replans,
        )
        record_dynamic_chain_failure(
            state,
            step,
            reason=reason,
            ocr_summary=str(state.get("last_ocr_summary") or ""),
            artifact=str(state.get("last_screenshot") or ""),
        )
        clear_dynamic_chain(state, failed=not will_replan)
        if will_replan:
            mark_tree_node_done(state, node, evidence=f"replan:{step.id}:{reason}")
            logger.warning(
                "[LaunchGraph:dynamic] step_stalled id=%s, replan from failure",
                step.id,
            )
            return state  # type: ignore[return-value]
        mark_tree_node_failed(state, node, reason)
        logger.warning("[LaunchGraph:dynamic] dynamic_exit no_progress=%d", no_progress)
        return state  # type: ignore[return-value]

    sw, sh = deps.screen_width, deps.screen_height
    if not sw or not sh:
        sw, sh = deps.adb.touch_size()
        deps.screen_width, deps.screen_height = sw, sh

    before_fp = str(state.get("dynamic_last_fingerprint") or "")
    exec_msg = _execute_dynamic_step(step, adb=deps.adb, sw=sw, sh=sh)
    deps.adb.wait_seconds(0.8)

    ts = datetime.now().strftime("%H%M%S_%f")
    shot = deps.artifact_root / f"graph_dynamic_{step.id}_{ts}.png"
    deps.adb.screencap_png(shot)
    actx = deps.attempt_context
    if actx is not None:
        actx.set_ocr_busy(True)
    try:
        ocr_summary, _ = await asyncio.to_thread(
            run_ocr_frame,
            shot,
            device_w=sw,
            device_h=sh,
            worker_key=deps.adb.device_serial,
        )
    finally:
        if actx is not None:
            actx.set_ocr_busy(False)

    after_fp = chain_progress_fingerprint(ocr_summary=ocr_summary, stage=step.label)
    progressed = not before_fp or after_fp != before_fp
    tap_ok = step.action != "tap_xy" or "Tapped" in exec_msg

    if progressed and tap_ok:
        mark_step_attempt(state, step, done=True)
        state["dynamic_no_progress"] = 0
        if step.label == "enter_world" or _ENTER_WORLD_RE.search(step.target_text or ocr_summary):
            increment_enter_tapped(state)
        advance_dynamic_cursor(state)
        logger.info(
            "[LaunchGraph:dynamic] step_done id=%s label=%s (%s,%s) | %s",
            step.id,
            step.label,
            step.x,
            step.y,
            exec_msg[:120],
        )
    else:
        mark_step_attempt(state, step, done=False)
        state["dynamic_no_progress"] = no_progress + 1
        note_action_failure(
            state,
            node=node,
            verify=NodeVerifyResult(
                passed=False,
                reason=f"dynamic step {step.id} stalled",
                evidence=f"progressed={progressed} tap_ok={tap_ok}",
            ),
            ocr_before=str(state.get("last_ocr_summary") or ""),
            ocr_after=ocr_summary,
            attempt=step.attempts,
            artifact=str(shot.resolve()),
            expected_stage=step.label,
        )
        cur = get_current_step(state)
        if cur is not None and cur.attempts >= limits.max_dynamic_step_attempts:
            reason = f"step {step.id} failed after {cur.attempts} attempts"
            will_replan = can_replan_dynamic_chain(
                state,
                max_replans=limits.max_dynamic_replans,
            )
            record_dynamic_chain_failure(
                state,
                cur,
                reason=reason,
                ocr_summary=ocr_summary,
                artifact=str(shot.resolve()),
            )
            logger.warning(
                "[LaunchGraph:dynamic] step_failed id=%s attempts=%d replan=%s",
                step.id,
                cur.attempts,
                will_replan,
            )
            clear_dynamic_chain(state, failed=not will_replan)
            if will_replan:
                mark_tree_node_done(
                    state,
                    node,
                    artifact=str(shot.resolve()),
                    evidence=f"replan:{step.id}:{reason}",
                )
                return state  # type: ignore[return-value]
            mark_tree_node_failed(
                state,
                node,
                reason,
                artifact=str(shot.resolve()),
            )
            return state  # type: ignore[return-value]
        logger.info(
            "[LaunchGraph:dynamic] step_retry id=%s label=%s progress=%s | %s",
            step.id,
            step.label,
            progressed,
            exec_msg[:120],
        )

    state["dynamic_last_fingerprint"] = after_fp
    state["last_screenshot"] = str(shot.resolve())
    state["last_ocr_summary"] = ocr_summary
    mark_tree_node_done(
        state,
        node,
        artifact=str(shot.resolve()),
        evidence=f"{step.id}:{step.label}:{step.reason}",
    )
    state["recover_hint"] = f"dynamic:{step.label}:{step.reason}"
    return state  # type: ignore[return-value]


def _execute_scene_action(
    plan,
    *,
    adb,
    sw: int,
    sh: int,
) -> str:
    if plan.action == "tap_xy":
        if plan.x <= 0 or plan.y <= 0:
            return f"refused tap invalid ({plan.x},{plan.y})"
        return adb.tap(plan.x, plan.y, width=sw, height=sh)
    if plan.action == "press_back":
        return adb.press_back()
    if plan.action == "wait":
        return adb.wait_seconds(plan.wait_s)
    if plan.action == "observe":
        return adb.wait_seconds(plan.wait_s)
    return "no-op"


async def scene_action_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    """场景策略单步：对话/教程/加载低成本推进，无固定点击阈值。"""
    state = dict(state)
    node = "scene_action"
    if state.get("session_agent_active"):
        mark_tree_node_done(state, node, evidence="session_agent_active_skip")
        return state  # type: ignore[return-value]
    scene_rounds = int(state.get("scene_rounds") or 0) + 1
    state["scene_rounds"] = scene_rounds

    sw, sh = deps.screen_width, deps.screen_height
    if not sw or not sh:
        sw, sh = deps.adb.touch_size()
        deps.screen_width, deps.screen_height = sw, sh

    scene_id = scene_id_from_scene_gate(
        state,
        fallback=str(state.get("active_scene_strategy") or state.get("scene_id") or "unknown"),
    )
    transition = SceneTransition(
        kind=str(state.get("scene_transition") or "none"),  # type: ignore[arg-type]
        reason=str(state.get("scene_transition_reason") or ""),
        from_scene=str(state.get("scene_id") or ""),
        to_scene=scene_id,
    )

    bboxes = deserialize_bboxes(state.get("last_bboxes"))
    ocr_summary = str(state.get("last_ocr_summary") or "")
    facts = facts_from_state(state)
    confidence = float(state.get("scene_confidence") or facts.scene_confidence or 0)

    shot_path = str(state.get("last_screenshot") or "")
    live_cls = classify_scene(
        facts,
        bboxes,
        ocr_summary=ocr_summary,
        screen_w=sw,
        screen_h=sh,
        screenshot_path=shot_path or None,
    )
    if live_cls.scene_id in ("dialogue", "tutorial") and live_cls.confidence >= 0.45:
        scene_id = live_cls.scene_id
        confidence = live_cls.confidence
    elif live_cls.scene_id == "loading" and live_cls.confidence >= 0.55:
        scene_id = "loading"
        confidence = live_cls.confidence

    blackout = bool(
        shot_path
        and is_secure_keyboard_blackout(
            Path(shot_path),
            bboxes,
            ocr_summary=ocr_summary,
        )
    )

    if is_pre_login_passive_wait(
        state,
        facts,
        scene_id=scene_id,
        confidence=confidence,
    ):
        plan = plan_loading_action(transition=transition)
        scene_id = "loading"
    elif (vlm_plan := plan_from_scene_gate(state, scene_id=scene_id)) is not None:
        plan = vlm_plan
    elif str(state.get("scene_label_coord_strategy") or "").strip() not in ("", "none"):
        from game_agent.models.scene_gate import SceneGateJudgment
        from game_agent.services.scene_label_executor import (
            motion_cfg_for_scene_label_pulse,
            plan_from_scene_label,
            should_run_pulse_for_label,
        )
        from game_agent.services.scene_label_registry import SceneLabelRegistry

        judgment_raw = state.get("last_scene_label_judgment")
        judgment = None
        if isinstance(judgment_raw, dict) and judgment_raw:
            try:
                judgment = SceneGateJudgment.model_validate(judgment_raw)
            except Exception:
                judgment = None
        matched_entry = None
        label_id = str(state.get("scene_label_id") or "")
        if label_id:
            reg = SceneLabelRegistry(deps.artifact_root, cfg=deps.app_config.game.scene_labels)
            matched_entry = next((e for e in reg.load_all() if e.label_id == label_id), None)

        motion_result = None
        motion_cfg = deps.app_config.game.motion_probe
        if should_run_pulse_for_label(judgment=judgment, matched_entry=matched_entry):
            from game_agent.services.motion_burst import capture_motion_burst
            from game_agent.services.motion_probe import run_motion_probe

            pulse_cfg = motion_cfg_for_scene_label_pulse(motion_cfg)
            burst = capture_motion_burst(
                deps.adb,
                deps.artifact_root,
                prefix=f"graph_scene_pulse_{scene_rounds:03d}",
                motion_cfg=pulse_cfg,
            )
            motion_result = await asyncio.to_thread(
                run_motion_probe,
                burst.frame_paths,
                artifact_root=deps.artifact_root,
                round_id=scene_rounds,
                motion_cfg=pulse_cfg,
            )

        dim_cfg = deps.app_config.game.dialogue_dim_tap
        dim_tap_xy = None
        advance_mode = "ocr"
        from game_agent.services.technique_selection_heuristics import is_technique_selection_screen

        dim_scene_allowed = scene_id == "dialogue" and not is_technique_selection_screen(ocr_summary)
        if dim_scene_allowed and shot_path:
            from game_agent.services.dialogue_advance_state import should_use_dim_region_tap

            if should_use_dim_region_tap(state, cfg=dim_cfg):
                from game_agent.services.dialogue_dim_locator import locate_dialogue_dim_tap

                dim_tap_xy = locate_dialogue_dim_tap(
                    Path(shot_path),
                    bboxes=bboxes,
                    screen_w=sw,
                    screen_h=sh,
                    cfg=dim_cfg,
                    artifact_root=deps.artifact_root,
                    annotate_name=f"dialogue_dim_scene_{scene_rounds:03d}.png",
                )
                if dim_tap_xy is not None:
                    advance_mode = "dim_region"

        plan = plan_from_scene_label(
            judgment=judgment.to_scene_label_judgment() if judgment else None,
            matched_entry=matched_entry,
            bboxes=bboxes,
            ocr_summary=ocr_summary,
            screen_w=sw,
            screen_h=sh,
            transition=transition,
            screenshot_path=shot_path or None,
            motion_result=motion_result,
            dim_tap_xy=dim_tap_xy,
            legacy_scene_id=scene_id,
        )
        if plan.action == "none":
            plan = plan_scene_action(
                scene_id,
                bboxes,
                ocr_summary=ocr_summary,
                screen_w=sw,
                screen_h=sh,
                transition=transition,
                advance_mode=advance_mode,
                dim_tap_xy=dim_tap_xy,
                screenshot_path=shot_path or None,
                motion_result=motion_result,
            )
    elif (
        scene_id == "loading"
        and blackout
        and should_handle_secure_keyboard_blackout(state)
    ):
        streak = blackout_streak(state)
        if should_press_back_for_blackout(streak):
            plan = SceneActionPlan(
                action="press_back",
                reason="loading:blackout_press_back",
                mode="advance",
            )
        else:
            wait_s = 3.0 if streak >= 2 else 2.5
            plan = SceneActionPlan(
                action="wait",
                wait_s=wait_s,
                reason=f"loading:blackout_wait_{max(streak, 1)}",
                mode="wait_observe",
            )
    else:
        dim_cfg = deps.app_config.game.dialogue_dim_tap
        dim_tap_xy = None
        advance_mode = "ocr"
        from game_agent.services.technique_selection_heuristics import (
            is_technique_selection_screen,
            should_allow_dim_region_in_game,
        )

        dim_scene_allowed = scene_id == "dialogue" and not is_technique_selection_screen(
            ocr_summary
        )
        if dim_scene_allowed and should_allow_dim_region_in_game(
            ui_stage="dialog",
            ocr_summary=ocr_summary,
            screen_analysis=None,
            state=state,
        ):
            from game_agent.services.dialogue_advance_state import should_use_dim_region_tap

            if should_use_dim_region_tap(state, cfg=dim_cfg) and shot_path:
                from game_agent.services.dialogue_dim_locator import locate_dialogue_dim_tap

                dim_tap_xy = locate_dialogue_dim_tap(
                    Path(shot_path),
                    bboxes=bboxes,
                    screen_w=sw,
                    screen_h=sh,
                    cfg=dim_cfg,
                    artifact_root=deps.artifact_root,
                    annotate_name=f"dialogue_dim_scene_{scene_rounds:03d}.png",
                )
                if dim_tap_xy is not None:
                    advance_mode = "dim_region"
        plan = plan_scene_action(
            scene_id,
            bboxes,
            ocr_summary=ocr_summary,
            screen_w=sw,
            screen_h=sh,
            transition=transition,
            advance_mode=advance_mode,
            dim_tap_xy=dim_tap_xy,
            screenshot_path=shot_path or None,
        )

    exec_msg = _execute_scene_action(plan, adb=deps.adb, sw=sw, sh=sh)
    if plan.action in ("tap_xy", "press_back"):
        deps.adb.wait_seconds(0.8)
    elif plan.action in ("wait", "observe"):
        deps.adb.wait_seconds(0.2)

    ts = datetime.now().strftime("%H%M%S_%f")
    after_shot = deps.artifact_root / f"graph_scene_{scene_id}_{ts}.png"
    deps.adb.screencap_png(after_shot)
    actx = deps.attempt_context
    if actx is not None:
        actx.set_ocr_busy(True)
    try:
        after_ocr, after_bboxes = await asyncio.to_thread(
            run_ocr_frame,
            after_shot,
            device_w=sw,
            device_h=sh,
            worker_key=deps.adb.device_serial,
        )
    finally:
        if actx is not None:
            actx.set_ocr_busy(False)

    after_facts = classify_screen_facts(
        after_bboxes,
        screen_w=sw,
        screen_h=sh,
        ocr_summary=after_ocr,
    )
    after_cls = classify_scene(
        after_facts,
        after_bboxes,
        ocr_summary=after_ocr,
        screen_w=sw,
        screen_h=sh,
        screenshot_path=after_shot,
    )
    after_transition = detect_scene_transition(
        prev_scene_id=str(state.get("scene_id") or "unknown"),
        prev_fingerprint=str(state.get("scene_fingerprint") or ""),
        classification=after_cls,
        facts=after_facts,
        ocr_summary=after_ocr,
        screenshot_path=after_shot,
    )
    prev_scene_fp = str(state.get("scene_fingerprint") or "")
    apply_scene_classification(state, after_cls, after_transition, after_facts)

    state["last_screenshot"] = str(after_shot.resolve())
    state["last_ocr_summary"] = after_ocr
    state["last_bboxes"] = serialize_bboxes(after_bboxes)
    state["facts"] = after_facts.model_dump()
    state["scene_last_action_signature"] = plan.signature()
    state["recover_hint"] = f"scene:{scene_id}:{plan.reason[:120]}"

    scene_no_progress = plan.action in ("tap_xy", "press_back") and ocr_summary.strip() == after_ocr.strip()
    if scene_id in ("dialogue", "tutorial") and scene_no_progress:
        fp_changed = bool(
            prev_scene_fp
            and after_cls.fingerprint
            and prev_scene_fp != after_cls.fingerprint
        )
        if fp_changed or ocr_progressed(ocr_summary, after_ocr):
            scene_no_progress = False
    if scene_no_progress:
        note_action_failure(
            state,
            node=node,
            verify=NodeVerifyResult(
                passed=False,
                reason="scene action no OCR change",
                evidence=f"scene={scene_id} action={plan.action}",
            ),
            ocr_before=ocr_summary,
            ocr_after=after_ocr,
            attempt=scene_rounds,
            artifact=str(after_shot.resolve()),
            expected_stage=scene_id,
        )

    if scene_id == "dialogue" and plan.action in ("tap_xy", "press_back"):
        from game_agent.services.dialogue_advance_state import (
            record_dialogue_advance_progress,
            set_dialogue_dim_last_tap,
        )

        progressed = not scene_no_progress
        used_dim = plan.mode == "dim_advance"
        record_dialogue_advance_progress(state, progressed=progressed, used_dim_tap=used_dim)
        if used_dim and plan.x > 0 and plan.y > 0:
            set_dialogue_dim_last_tap(state, plan.x, plan.y)

    if plan.action == "tap_xy" and plan.x > 0 and not scene_no_progress:
        from game_agent.models.scene_label import SceneLabelTraceEvent
        from game_agent.services.behavior_chain import BehaviorStep
        from game_agent.services.scene_label_runner import learn_scene_label_after_step
        from game_agent.services.scene_label_registry import SceneLabelRegistry

        learn_scene_label_after_step(
            state,
            artifact_root=deps.artifact_root,
            before_ocr=ocr_summary,
            after_ocr=after_ocr,
            bboxes=bboxes,
            step=BehaviorStep(
                id="scene_label_tap",
                action="tap_xy",
                x=plan.x,
                y=plan.y,
                reason=plan.reason,
            ),
            round_id=scene_rounds,
            screenshot_ref=str(after_shot.resolve()),
            screen_w=sw,
            screen_h=sh,
            step_passed=True,
            scope="pre_enter",
            cfg=deps.app_config.game.scene_labels,
        )
        SceneLabelRegistry(deps.artifact_root, cfg=deps.app_config.game.scene_labels).log_trace(
            SceneLabelTraceEvent(
                round_id=scene_rounds,
                node="scene_action",
                vlm_label_slug=str(state.get("scene_label_slug") or ""),
                matched_label_id=str(state.get("scene_label_id") or ""),
                is_new_label=not bool(state.get("scene_label_id")),
                coord_strategy=str(state.get("scene_label_coord_strategy") or ""),
                semantic_target=str(state.get("scene_label_semantic_target") or ""),
                tap_x=plan.x,
                tap_y=plan.y,
                progressed=True,
                screenshot_ref=str(after_shot.resolve()),
                ocr_head=ocr_summary[:120],
            )
        )

    logger.info(
        "[LaunchGraph:scene] round=%d scene=%s action=%s mode=%s (%s,%s) | %s",
        scene_rounds,
        scene_id,
        plan.action,
        plan.mode,
        plan.x,
        plan.y,
        exec_msg[:120],
    )

    mark_tree_node_done(
        state,
        node,
        artifact=str(after_shot.resolve()),
        evidence=f"{scene_id}:{plan.action}:{plan.reason[:120]}",
    )
    return state  # type: ignore[return-value]


async def session_relogin_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    """进程重启后简化重登：OCR 探测 + VLM 单步，必要时委托 atomic_login。"""
    state = dict(state)
    node = "session_relogin"
    limits = launch_graph_limits_from_state(state)
    relogin_rounds = int(state.get("session_relogin_rounds") or 0) + 1
    state["session_relogin_rounds"] = relogin_rounds

    if relogin_rounds > limits.max_free_rounds:
        mark_tree_node_failed(state, node, f"session_relogin max rounds ({limits.max_free_rounds})")
        state["recover_hint"] = f"session_relogin exhausted after {limits.max_free_rounds} rounds"
        logger.warning("[LaunchGraph:session_relogin] exit max_rounds=%d", limits.max_free_rounds)
        return state  # type: ignore[return-value]

    sw, sh = deps.screen_width, deps.screen_height
    if not sw or not sh:
        sw, sh = deps.adb.touch_size()
        deps.screen_width, deps.screen_height = sw, sh

    actx = deps.attempt_context
    if actx is not None:
        actx.set_ocr_busy(True)
    try:
        ts = datetime.now().strftime("%H%M%S_%f")
        shot = deps.artifact_root / f"graph_session_relogin_{ts}.png"
        deps.adb.screencap_png(shot)
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

    from game_agent.services.login_stage_probe import probe_login_stage
    from game_agent.services.free_action_planner import (
        decide_free_action_heuristic,
        decide_session_relogin_action,
        FreeActionPlan,
    )
    from game_agent.services.session_restart_recovery import (
        format_checkpoint_for_prompt,
        maybe_complete_session_relogin_recovery,
    )
    from game_agent.workers.vision_worker import VisionWorker

    probe = probe_login_stage(bboxes, screen_w=sw, screen_h=sh)
    if probe.stage == "login_form" and probe.blocking:
        logger.info("[LaunchGraph:session_relogin] delegate → atomic_login (%s)", probe.reason[:80])
        mark_tree_node_done(state, node, evidence=f"delegate:atomic_login:{probe.reason[:80]}")
        return await atomic_login_node(state, deps)

    facts = classify_screen_facts(bboxes, screen_w=sw, screen_h=sh, ocr_summary=ocr_summary)
    if facts.sub_account_blocking:
        logger.info("[LaunchGraph:session_relogin] delegate → select_sub_account")
        mark_tree_node_done(state, node, evidence="delegate:select_sub_account")
        return await select_sub_account_node(state, deps)

    if enter_gate_likely_visible(bboxes, ocr_merged=ocr_summary):
        tap_decision = await decide_enter_gate_tap(
            llm_cfg=deps.app_config.llm,
            bboxes=bboxes,
            ocr_summary=ocr_summary,
            stage_hint="session_relogin; tap enter-game CTA",
            screen_w=sw,
            screen_h=sh,
            deepseek=deps.app_config.deepseek,
        )
        if tap_decision is not None:
            exec_msg = deps.adb.tap(tap_decision.x, tap_decision.y, width=sw, height=sh)
            deps.adb.wait_seconds(0.8)
            increment_enter_tapped(state)
            mark_tree_node_done(
                state,
                node,
                artifact=str(shot.resolve()),
                evidence=f"enter_gate:({tap_decision.x},{tap_decision.y})",
            )
            state["recover_hint"] = f"session_relogin:enter_gate:{exec_msg[:80]}"
            maybe_complete_session_relogin_recovery(state, facts)
            return state  # type: ignore[return-value]

    if await _try_confirm_in_game_via_hud_ocr(
        state,
        deps,
        ocr_summary=ocr_summary,
        shot_path=shot,
        round_id=relogin_rounds,
        source="session_relogin",
    ):
        mark_tree_node_done(state, node, artifact=str(shot.resolve()), evidence="hud_in_game")
        maybe_complete_session_relogin_recovery(state, facts)
        return state  # type: ignore[return-value]

    prior_sig = str(state.get("session_relogin_last_action_signature") or "")
    phase = str(state.get("session_restart_phase") or "post_login_in_game")
    checkpoint_text = format_checkpoint_for_prompt(state.get("session_restart_checkpoint"))
    plan = decide_free_action_heuristic(bboxes, ocr_summary=ocr_summary)
    if plan is None and deps.app_config.llm_multimodal is not None:
        vision = VisionWorker(deps.app_config.llm_multimodal, attempt_context=deps.attempt_context)
        plan = await decide_session_relogin_action(
            vision,
            screenshot_path=shot,
            ocr_summary=ocr_summary,
            round_id=relogin_rounds,
            prior_action_signature=prior_sig,
            phase=phase,
            checkpoint_text=checkpoint_text,
        )
    if plan is None:
        plan = FreeActionPlan(
            action="wait",
            wait_s=2.5,
            reason="no heuristic/vision plan",
            stage="unknown",
        )

    exec_msg = _execute_free_action(plan, adb=deps.adb, sw=sw, sh=sh)
    deps.adb.wait_seconds(0.8)
    if plan.action in ("tap_xy", "tap_text") and "Tapped" in exec_msg:
        if _ENTER_WORLD_RE.search(plan.target_text or ocr_summary):
            increment_enter_tapped(state)

    sig = plan.signature()
    state["session_relogin_last_action_signature"] = sig
    logger.info(
        "[LaunchGraph:session_relogin] round=%d action=%s (%s,%s) reason=%s | %s",
        relogin_rounds,
        plan.action,
        plan.x,
        plan.y,
        plan.reason[:100],
        exec_msg[:120],
    )
    mark_tree_node_done(
        state,
        node,
        artifact=str(shot.resolve()),
        evidence=f"{plan.action}:{plan.reason[:120]}",
    )
    state["recover_hint"] = f"session_relogin:{plan.reason[:200]}"
    state["facts"] = facts.model_dump()
    maybe_complete_session_relogin_recovery(state, facts)
    return state  # type: ignore[return-value]


async def free_node(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    """登录后兜底：OCR+多模态规划单步动作，推进创角/选角/加载等可变流程。"""
    state = dict(state)
    node = "free"
    limits = launch_graph_limits_from_state(state)
    free_rounds = int(state.get("free_rounds") or 0) + 1
    state["free_rounds"] = free_rounds

    if free_rounds > limits.max_free_rounds:
        mark_tree_node_failed(state, node, f"free max rounds ({limits.max_free_rounds})")
        state["recover_hint"] = f"free exhausted after {limits.max_free_rounds} rounds"
        logger.warning("[LaunchGraph:free] free_exit max_rounds=%d", limits.max_free_rounds)
        return state  # type: ignore[return-value]

    no_progress = int(state.get("free_no_progress_rounds") or 0)
    if no_progress >= limits.max_free_no_progress_rounds:
        mark_tree_node_failed(state, node, f"free no progress ({limits.max_free_no_progress_rounds})")
        state["recover_hint"] = "free stalled without UI progress"
        logger.warning("[LaunchGraph:free] free_exit no_progress=%d", no_progress)
        return state  # type: ignore[return-value]

    sw, sh = deps.screen_width, deps.screen_height
    if not sw or not sh:
        sw, sh = deps.adb.touch_size()
        deps.screen_width, deps.screen_height = sw, sh

    before_fp = str(state.get("free_last_progress_fingerprint") or "")
    before_stage = str(state.get("current_stage") or "")

    actx = deps.attempt_context
    if actx is not None:
        actx.set_ocr_busy(True)
    try:
        ts = datetime.now().strftime("%H%M%S_%f")
        shot = deps.artifact_root / f"graph_free_{ts}.png"
        deps.adb.screencap_png(shot)
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

    if await _try_confirm_in_game_via_hud_ocr(
        state,
        deps,
        ocr_summary=ocr_summary,
        shot_path=shot,
        round_id=free_rounds,
        source="free",
    ):
        mark_tree_node_done(
            state,
            node,
            artifact=str(shot.resolve()),
            evidence=f"hud_trigger:{','.join(state.get('free_in_game_ocr_hits') or [])}",
        )
        state["recover_hint"] = f"free:in_game_entry_passed:{','.join(state.get('free_in_game_ocr_hits') or [])}"
        return state  # type: ignore[return-value]

    prior_sig = str(state.get("free_last_action_signature") or "")
    same_streak = int(state.get("free_same_action_streak") or 0)

    vision = None
    if deps.app_config.llm_multimodal is not None:
        vision = VisionWorker(deps.app_config.llm_multimodal, attempt_context=deps.attempt_context)

    plan = await decide_free_action(
        vision=vision,
        screenshot_path=shot,
        bboxes=bboxes,
        ocr_summary=ocr_summary,
        round_id=free_rounds,
        prior_action_signature=prior_sig if same_streak >= 1 else "",
    )

    sig = plan.signature()
    if sig == prior_sig:
        same_streak += 1
    else:
        same_streak = 1
    state["free_same_action_streak"] = same_streak

    if same_streak >= limits.max_free_same_action and plan.action in ("tap_xy", "tap_text"):
        logger.info(
            "[LaunchGraph:free] same action x%d, switch to wait | %s",
            same_streak,
            sig,
        )
        plan = FreeActionPlan(
            action="wait",
            wait_s=2.5,
            reason=f"dedupe after repeat {sig}",
            stage=plan.stage,
        )
        sig = plan.signature()
        same_streak = 0
        state["free_same_action_streak"] = 0

    exec_msg = _execute_free_action(plan, adb=deps.adb, sw=sw, sh=sh)
    deps.adb.wait_seconds(0.8)

    if plan.action in ("tap_xy", "tap_text") and "Tapped" in exec_msg:
        if _ENTER_WORLD_RE.search(plan.target_text or ocr_summary):
            increment_enter_tapped(state)

    after_fp = compute_progress_fingerprint(
        current_stage=before_stage,
        ocr_summary=ocr_summary,
        vision_stage=plan.stage,
    )
    if before_fp and after_fp == before_fp:
        state["free_no_progress_rounds"] = no_progress + 1
        note_action_failure(
            state,
            node=node,
            verify=NodeVerifyResult(
                passed=False,
                reason="free action stalled",
                evidence=f"action={plan.action} sig={sig}",
            ),
            ocr_before=ocr_summary,
            ocr_after=ocr_summary,
            attempt=free_rounds,
            artifact=str(shot.resolve()),
            expected_stage=plan.stage,
        )
        logger.info(
            "[LaunchGraph:free] free_progress stalled round=%d streak=%d",
            free_rounds,
            state["free_no_progress_rounds"],
        )
    else:
        state["free_no_progress_rounds"] = 0
        logger.info(
            "[LaunchGraph:free] free_progress ok round=%d stage=%s",
            free_rounds,
            plan.stage,
        )
    state["free_last_progress_fingerprint"] = after_fp
    state["free_last_action_signature"] = sig

    logger.info(
        "[LaunchGraph:free] free_action round=%d action=%s (%s,%s) reason=%s | %s",
        free_rounds,
        plan.action,
        plan.x,
        plan.y,
        plan.reason[:100],
        exec_msg[:120],
    )

    mark_tree_node_done(
        state,
        node,
        artifact=str(shot.resolve()),
        evidence=f"{plan.action}:{plan.reason[:120]}",
    )
    state["recover_hint"] = f"free:{plan.reason[:200]}"
    return state  # type: ignore[return-value]


_ENTER_WORLD_RE = compile_lexicon_pattern(Concept.ENTER_WORLD, Concept.START_GAME)


async def recover_from_failure_node(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
) -> LaunchGraphState:
    state = dict(state)
    node = "recover_from_failure"
    root = _artifact_log_root(deps)
    external_summary = await _load_external_log_summary(deps, limit=100)
    _store_external_log_summary(state, external_summary)
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

    ocr_merged = str(state.get("last_ocr_summary") or "")
    on_enter_gate_past_login = (
        failed_name == "atomic_login"
        and facts.login_stage != "login_form"
        and not facts.login_blocking
        and (
            facts.enter_cta_visible
            or bool(_ENTER_WORLD_RE.search(ocr_merged))
        )
    )
    if on_enter_gate_past_login:
        set_login_done(state, evidence="recover: enter gate past credential login")
        deps.run_state.launch_stage = "server_select"
        _clear_failed("atomic_login")
        clear_game_entry_judgment(state)
        state["recover_hint"] = f"{hint}; enter_gate_continue"
        logger.info("[LaunchGraph:recover] enter gate past login — clear atomic_login failed")
        mark_tree_node_done(state, node)
        return state  # type: ignore[return-value]

    if is_login_flow_in_progress(state):
        ts = datetime.now().strftime("%H%M%S_%f")
        shot = deps.artifact_root / f"graph_recover_{ts}.png"
        deps.adb.screencap_png(shot)
        from game_agent.utils.screen_coord import resolve_and_sync

        space = resolve_and_sync(deps.adb, shot, deps=deps, state=state)
        sw, sh = space.tap_w, space.tap_h
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

        login_blackout = is_secure_keyboard_blackout(
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

        if failed_name == "atomic_login" and not login_blackout:
            dismiss_msg = await asyncio.to_thread(
                try_dismiss_login_secure_keyboard,
                deps.adb,
                deps.app_config.executor,
            )
            hint += f"; pre-retry dismiss={dismiss_msg[:120]}"
            logger.info("[LaunchGraph:recover] atomic_login pre-retry dismiss: %s", dismiss_msg[:200])

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
        or (failed_name == "atomic_login" and facts.login_stage == "login_form")
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
            facts, vision_hint = merge_analyze_screen_response(
                facts,
                analyze_json,
                bboxes=deserialize_bboxes(state.get("last_bboxes")),
                screen_w=int(deps.screen_width or 0),
                ocr_merged=str(state.get("last_ocr_summary") or ""),
            )
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
