"""通用阶段模板引擎：think → decide → materialize → act → verify/mark。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from game_agent.graphs.launch_deps import LaunchGraphDeps
from game_agent.models.launch_graph_state import LaunchGraphState, facts_from_state
from game_agent.models.phase_template import PhaseSpec, compute_phase_fingerprint
from game_agent.models.settings import LaunchGraphSection
from game_agent.services.adaptive_tree import (
    DecideOutcomeKind,
    adaptive_tree_trace,
    commit_tree_node,
    completed_phases_summary,
    decide_phase_spec,
    fail_active_tree_node,
    get_active_spec,
    get_active_tree_node,
    increment_active_attempts,
    mark_adaptive_flow_done,
    mark_adaptive_parent_done,
    mark_adaptive_parent_failed,
    materialize_tree_node,
)
from game_agent.services.phase_completion import evaluate_phase_complete, execute_phase_action
from game_agent.services.dismiss_blank_modal import execute_dismiss_blank_modal
from game_agent.services.phase_planner import plan_phase_spec
from game_agent.utils.ocr_util import deserialize_bboxes, run_ocr_frame, serialize_bboxes
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)


def _launch_limits(deps: LaunchGraphDeps) -> LaunchGraphSection:
    return deps.app_config.launch_graph


async def _capture_ocr(
    deps: LaunchGraphDeps,
    shot_path,
    *,
    actx,
    state: LaunchGraphState | None = None,
) -> tuple[str, list]:
    from game_agent.utils.screen_coord import resolve_and_sync

    st = state if state is not None else {}
    space = resolve_and_sync(deps.adb, shot_path, deps=deps, state=st)
    sw, sh = space.tap_w, space.tap_h
    if actx is not None:
        actx.set_ocr_busy(True)
    try:
        ocr_summary, bboxes = await asyncio.to_thread(
            run_ocr_frame,
            shot_path,
            device_w=sw,
            device_h=sh,
            worker_key=deps.adb.device_serial,
        )
    finally:
        if actx is not None:
            actx.set_ocr_busy(False)
    return ocr_summary, bboxes


async def _think_phase_spec(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
    *,
    shot,
    ocr_summary: str,
    rounds: int,
    stall_hint: str,
) -> PhaseSpec | None:
    llm_cfg = deps.app_config.llm_multimodal
    if llm_cfg is None:
        return None
    vision = VisionWorker(llm_cfg, attempt_context=deps.attempt_context)
    return await plan_phase_spec(
        vision,
        screenshot_path=shot,
        ocr_summary=ocr_summary,
        completed_phases_summary=completed_phases_summary(state),
        prior_phase_summary="",
        stall_hint=stall_hint,
        login_done=bool(state.get("login_done")),
        enter_tapped_count=int(state.get("enter_tapped_count") or 0),
        round_id=rounds,
    )


async def _run_think_materialize(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
    *,
    shot,
    ocr_summary: str,
    rounds: int,
) -> LaunchGraphState:
    limits = _launch_limits(deps)
    stall = int(state.get("adaptive_no_progress") or 0)
    stall_hint = ""
    if stall >= 1:
        stall_hint = (
            f"no progress x{stall}; do NOT repeat completed phase_id; "
            "pick a different tap or wait"
        )

    planned = await _think_phase_spec(
        state,
        deps,
        shot=shot,
        ocr_summary=ocr_summary,
        rounds=rounds,
        stall_hint=stall_hint,
    )
    if planned is None:
        mark_adaptive_parent_failed(state, "phase plan parse failed")
        state["recover_hint"] = "adaptive plan parse failed"
        return state  # type: ignore[return-value]

    outcome = decide_phase_spec(
        state,
        planned,
        min_confidence=limits.adaptive_min_confidence,
    )

    if outcome.kind == DecideOutcomeKind.FLOW_DONE:
        mark_adaptive_flow_done(state, evidence=outcome.spec.reason[:500] if outcome.spec else "adaptive skip")
        mark_adaptive_parent_done(state, evidence=outcome.spec.reason[:500] if outcome.spec else "adaptive skip")
        logger.info("[PhaseEngine] flow inactive | %s", (outcome.reason or "")[:200])
        return state  # type: ignore[return-value]

    if outcome.kind == DecideOutcomeKind.REJECT_DUPLICATE:
        state["adaptive_no_progress"] = stall + 1
        logger.info("[PhaseEngine] reject duplicate %s", outcome.reason)
        if deps.audit is not None:
            deps.audit.log_observer(
                kind="adaptive_tree_stall",
                message=outcome.reason[:200],
                round_id=rounds,
            )
        if int(state.get("adaptive_no_progress") or 0) >= limits.max_adaptive_no_progress:
            state["phase_replan_count"] = int(state.get("phase_replan_count") or 0) + 1
            state["adaptive_no_progress"] = 0
            if int(state["phase_replan_count"]) >= limits.max_adaptive_replan:
                mark_adaptive_parent_failed(state, "adaptive replan exhausted")
                state["recover_hint"] = "adaptive stalled, fallback free"
        return state  # type: ignore[return-value]

    spec = outcome.spec
    if spec is None:
        mark_adaptive_parent_failed(state, "decide returned no spec")
        return state  # type: ignore[return-value]

    entry_fp = compute_phase_fingerprint(ocr_summary=ocr_summary, phase_label=spec.phase_label)
    node = materialize_tree_node(
        state,
        spec,
        entry_fingerprint=entry_fp,
        created_round=rounds,
    )
    state["tree_trace"] = adaptive_tree_trace(state)
    if deps.audit is not None:
        deps.audit.log_observer(
            kind="adaptive_tree_materialize",
            message=node.phase_label or node.phase_id,
            round_id=rounds,
            extra=node.model_dump(),
        )
    logger.info(
        "[PhaseEngine] materialize %s label=%s action=%s conf=%.2f",
        node.node_id,
        node.phase_label,
        spec.action,
        spec.confidence,
    )
    state["recover_hint"] = f"adaptive:{node.phase_label or node.phase_id}"
    return state  # type: ignore[return-value]


async def _run_act_verify(
    state: LaunchGraphState,
    deps: LaunchGraphDeps,
    *,
    rounds: int,
    sw: int,
    sh: int,
    actx,
) -> LaunchGraphState:
    fg_cfg = deps.app_config.foreground_guard
    if fg_cfg.enabled:
        from game_agent.modules.run_context import block_until_foreground_ready

        if not block_until_foreground_ready(actx, poll_interval_s=fg_cfg.poll_interval_s):
            state["recover_hint"] = (
                actx.get_fatal_reason() if actx is not None else "foreground guard stop"
            ) or "foreground guard stop"
            state["finished"] = True
            state["terminal_error"] = str(state["recover_hint"])[:2000]
            return state  # type: ignore[return-value]

    limits = _launch_limits(deps)
    active = get_active_tree_node(state)
    spec = get_active_spec(state)
    if active is None or spec is None:
        state["recover_hint"] = "adaptive: no active node"
        return state  # type: ignore[return-value]

    entry_fp = active.entry_fingerprint or str(state.get("phase_entry_fingerprint") or "")
    if spec.action == "dismiss_blank":
        facts = facts_from_state(state)
        exec_msg, action_executed = execute_dismiss_blank_modal(
            spec,
            adb=deps.adb,
            sw=sw,
            sh=sh,
            ocr_summary=str(state.get("last_ocr_summary") or ""),
            bboxes=deserialize_bboxes(state.get("last_bboxes")),
            enter_cta_xy=facts.enter_cta_xy,
        )
    else:
        exec_msg, action_executed = execute_phase_action(spec, adb=deps.adb, sw=sw, sh=sh)
    if spec.action in ("tap_xy", "press_back", "dismiss_blank") and action_executed:
        deps.adb.wait_seconds(0.8)
    elif spec.action == "wait" and action_executed:
        deps.adb.wait_seconds(0.2)

    after_ts = datetime.now().strftime("%H%M%S_%f")
    after_shot = deps.artifact_root / f"graph_adaptive_{spec.phase_id}_{after_ts}.png"
    deps.adb.screencap_png(after_shot)
    after_ocr, _ = await _capture_ocr(deps, after_shot, actx=actx)
    after_fp = compute_phase_fingerprint(ocr_summary=after_ocr, phase_label=spec.phase_label)
    state["phase_last_fingerprint"] = after_fp
    state["last_screenshot"] = str(after_shot.resolve())
    state["last_ocr_summary"] = after_ocr

    if evaluate_phase_complete(
        spec,
        entry_fingerprint=entry_fp,
        after_fingerprint=after_fp,
        ocr_summary=after_ocr,
        action_executed=action_executed,
    ):
        evidence = f"{spec.action}:{spec.reason[:200]}|{exec_msg[:120]}"
        commit_tree_node(
            state,
            active,
            artifact=str(after_shot.resolve()),
            evidence=evidence,
        )
        state["tree_trace"] = adaptive_tree_trace(state)
        if deps.audit is not None:
            deps.audit.log_observer(
                kind="adaptive_tree_commit",
                message=active.phase_label or active.phase_id,
                round_id=rounds,
                extra={"node_id": active.node_id, "evidence": evidence[:300]},
            )
        logger.info(
            "[PhaseEngine] commit %s label=%s | %s",
            active.node_id,
            active.phase_label,
            evidence[:120],
        )
        state["recover_hint"] = f"adaptive:{active.phase_label or active.phase_id}"
        return state  # type: ignore[return-value]

    stall = int(state.get("adaptive_no_progress") or 0)
    if entry_fp and after_fp == entry_fp and spec.action != "wait":
        state["adaptive_no_progress"] = stall + 1
        increment_active_attempts(state)
        logger.info(
            "[PhaseEngine] stalled round=%d phase=%s streak=%d",
            rounds,
            spec.phase_id,
            state["adaptive_no_progress"],
        )
    else:
        state["adaptive_no_progress"] = 0

    if int(state.get("adaptive_no_progress") or 0) >= limits.max_adaptive_no_progress:
        fail_active_tree_node(state, reason=f"stall on {active.node_id}")
        state["phase_replan_count"] = int(state.get("phase_replan_count") or 0) + 1
        state["adaptive_no_progress"] = 0
        state["tree_trace"] = adaptive_tree_trace(state)
        if deps.audit is not None:
            deps.audit.log_observer(
                kind="adaptive_tree_stall",
                message=active.node_id,
                round_id=rounds,
            )
        if int(state["phase_replan_count"]) >= limits.max_adaptive_replan:
            mark_adaptive_parent_failed(state, "adaptive replan exhausted")
            state["recover_hint"] = "adaptive stalled, fallback free"
            return state  # type: ignore[return-value]
        logger.info("[PhaseEngine] clear active for replan (stall)")

    state["recover_hint"] = f"adaptive:{active.phase_label or active.phase_id}"
    return state  # type: ignore[return-value]


async def run_once(state: LaunchGraphState, deps: LaunchGraphDeps) -> LaunchGraphState:
    """adaptive_phase 单轮：有 active 则 act→verify，否则 think→decide→materialize。"""
    state = dict(state)
    limits = _launch_limits(deps)
    actx = deps.attempt_context

    if state.get("adaptive_flow_done"):
        return state  # type: ignore[return-value]

    rounds = int(state.get("adaptive_rounds") or 0) + 1
    state["adaptive_rounds"] = rounds
    if rounds > limits.max_adaptive_rounds:
        err = f"adaptive max rounds ({limits.max_adaptive_rounds})"
        mark_adaptive_parent_failed(state, err)
        state["recover_hint"] = err
        logger.warning("[PhaseEngine] %s", err)
        return state  # type: ignore[return-value]

    llm_cfg = deps.app_config.llm_multimodal
    if llm_cfg is None:
        mark_adaptive_parent_failed(state, "llm_multimodal not configured")
        mark_adaptive_flow_done(state, evidence="llm_multimodal not configured")
        return state  # type: ignore[return-value]

    sw, sh = deps.screen_width, deps.screen_height
    if not sw or not sh:
        sw, sh = deps.adb.touch_size()
        deps.screen_width, deps.screen_height = sw, sh

    active_id = str(state.get("adaptive_active_node_id") or "").strip()
    if active_id:
        return await _run_act_verify(state, deps, rounds=rounds, sw=sw, sh=sh, actx=actx)

    ts = datetime.now().strftime("%H%M%S_%f")
    shot = deps.artifact_root / f"graph_adaptive_round_{rounds:03d}_{ts}.png"
    deps.    adb.screencap_png(shot)
    ocr_summary, bboxes = await _capture_ocr(deps, shot, actx=actx, state=state)
    state["last_screenshot"] = str(shot.resolve())
    state["last_ocr_summary"] = ocr_summary
    state["last_bboxes"] = serialize_bboxes(bboxes)

    return await _run_think_materialize(
        state,
        deps,
        shot=shot,
        ocr_summary=ocr_summary,
        rounds=rounds,
    )
