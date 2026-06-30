"""Scene label 快路径执行（pre_enter + in_game）。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from game_agent.graphs.launch_state_store import mark_tree_node_done
from game_agent.models.launch_graph_state import LaunchGraphState
from game_agent.models.scene import SceneTransition
from game_agent.models.scene_label import SceneLabelJudgment, SceneLabelScope, SceneLabelTraceEvent
from game_agent.models.scene_labels_config import SceneLabelsSection
from game_agent.services.behavior_chain import (
    execute_behavior_step,
    sanitize_dialogue_blank_tap,
    sanitize_press_back_step,
)
from game_agent.services.in_game_agent import set_in_game_behavior_chain
from game_agent.services.scene_label_executor import plan_from_scene_label
from game_agent.services.scene_label_registry import SceneLabelRegistry
from game_agent.services.scene_memory_playbook import build_chain_from_memory, verify_memory_progress
from game_agent.services.scene_memory_store import SceneMemoryStore
from game_agent.utils.ocr_util import OcrBbox, run_ocr_frame

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SceneLabelFastPathResult:
    handled: bool
    success: bool = False
    message: str = ""


def _registry(artifact_root: Path, cfg: SceneLabelsSection | None = None) -> SceneLabelRegistry:
    return SceneLabelRegistry(artifact_root, cfg=cfg)


async def try_scene_label_fast_path(
    state: LaunchGraphState,
    *,
    shot: Path,
    ocr_summary: str,
    bboxes: list[OcrBbox],
    round_id: int,
    sw: int,
    sh: int,
    artifact_root: Path,
    adb,
    actx,
    audit,
    scope: SceneLabelScope,
    node: str = "scene_action",
    cfg: SceneLabelsSection | None = None,
    motion_result=None,
) -> SceneLabelFastPathResult:
    reg = _registry(artifact_root, cfg)
    hint_id = str(state.get("scene_label_id") or "")
    match = reg.retrieve(
        ocr_summary=ocr_summary,
        bboxes=bboxes,
        screen_h=sh,
        scope=scope,
        vlm_hint_label_id=hint_id,
    )
    if match is None:
        return SceneLabelFastPathResult(handled=False)

    transition = SceneTransition(kind="none", reason="", from_scene="", to_scene="")
    plan = plan_from_scene_label(
        judgment=None,
        matched_entry=match.entry,
        bboxes=bboxes,
        ocr_summary=ocr_summary,
        screen_w=sw,
        screen_h=sh,
        transition=transition,
        screenshot_path=shot,
        motion_result=motion_result,
    )
    if plan.action != "tap_xy" or plan.x <= 0 or plan.y <= 0:
        return SceneLabelFastPathResult(handled=False, message="label_plan_unresolved")

    if scope == "in_game":
        from game_agent.models.scene_memory import SceneMemoryEntry

        mem_entry = SceneMemoryEntry(
            memory_id=match.entry.label_id,
            archetype="dialogue_narrative",  # type: ignore[arg-type]
            structural_fingerprint=match.entry.structural_fingerprint,
            ocr_skeleton=match.entry.ocr_skeleton,
            primary_action=match.entry.execution_policy,
            success_count=match.entry.success_count,
            confidence=match.entry.confidence,
        )
        dim_xy = None
        if match.entry.coord_strategy == "dim_region":
            from game_agent.models.dialogue_dim_tap import DialogueDimTapSection
            from game_agent.services.dialogue_dim_locator import locate_dialogue_dim_tap

            dim_xy = locate_dialogue_dim_tap(
                shot,
                bboxes=bboxes,
                screen_w=sw,
                screen_h=sh,
                cfg=DialogueDimTapSection(),
                artifact_root=artifact_root,
                annotate_name=f"scene_label_dim_{round_id:03d}.png",
            )
        chain = build_chain_from_memory(
            mem_entry,
            bboxes=bboxes,
            screen_w=sw,
            screen_h=sh,
            dim_xy=dim_xy,
        )
        if chain is None:
            exec_msg = adb.tap(plan.x, plan.y, width=sw, height=sh)
        else:
            set_in_game_behavior_chain(state, chain)
            step = chain.steps[0]
            step = sanitize_press_back_step(step, ocr_summary=ocr_summary)
            step = sanitize_dialogue_blank_tap(
                step,
                ocr_summary=ocr_summary,
                bboxes=bboxes,
                screen_w=sw,
                screen_h=sh,
            )
            exec_msg = execute_behavior_step(step, adb=adb, sw=sw, sh=sh)
    else:
        exec_msg = adb.tap(plan.x, plan.y, width=sw, height=sh)

    if plan.action not in ("wait", "none"):
        adb.wait_seconds(0.6)

    ts = datetime.now().strftime("%H%M%S_%f")
    after_shot = artifact_root / f"graph_scene_label_{round_id:03d}_{ts}.png"
    adb.screencap_png(after_shot)
    if actx is not None:
        actx.set_ocr_busy(True)
    try:
        after_ocr, _ = await asyncio.to_thread(
            run_ocr_frame,
            after_shot,
            device_w=sw,
            device_h=sh,
            worker_key=adb.device_serial,
        )
    finally:
        if actx is not None:
            actx.set_ocr_busy(False)

    progressed = (ocr_summary or "").strip() != (after_ocr or "").strip()
    action_ok = "refused" not in str(exec_msg).lower()
    step_passed = progressed and action_ok

    reg.log_trace(
        SceneLabelTraceEvent(
            round_id=round_id,
            node=node,
            vlm_label_slug=match.entry.label_slug,
            matched_label_id=match.entry.label_id,
            is_new_label=False,
            coord_strategy=match.entry.coord_strategy,
            semantic_target=match.entry.semantic_target,
            tap_x=plan.x,
            tap_y=plan.y,
            progressed=step_passed,
            screenshot_ref=str(after_shot),
            ocr_head=(ocr_summary or "")[:120],
        )
    )

    if step_passed:
        reg.reinforce_verified(match.entry.label_id)
        hits = int(state.get("scene_label_hits") or state.get("scene_memory_hits") or 0) + 1
        state["scene_label_hits"] = hits
        state["scene_memory_hits"] = hits
        state["last_scene_label_match"] = {
            "label_id": match.entry.label_id,
            "label_slug": match.entry.label_slug,
            "similarity": match.similarity,
            "tap": [plan.x, plan.y],
            "fast_path": True,
        }
        state["scene_label_slug"] = match.entry.label_slug
        state["scene_label_id"] = match.entry.label_id
        state["last_screenshot"] = str(after_shot.resolve())
        state["last_ocr_summary"] = after_ocr
        if scope == "in_game":
            state["in_game_play_steps_executed"] = int(state.get("in_game_play_steps_executed") or 0) + 1
        logger.info(
            "[SceneLabel] fast_path ok slug=%s tap=(%d,%d) | %s",
            match.entry.label_slug,
            plan.x,
            plan.y,
            str(exec_msg)[:80],
        )
        if audit is not None:
            audit.log_phase(
                "scene_label",
                "fast_path",
                label_slug=match.entry.label_slug,
                label_id=match.entry.label_id,
                tap_x=plan.x,
                tap_y=plan.y,
            )
        mark_tree_node_done(
            state,
            node,
            artifact=str(after_shot.resolve()),
            evidence=f"scene_label:{match.entry.label_slug}:{plan.x},{plan.y}",
        )
        return SceneLabelFastPathResult(handled=True, success=True, message=str(exec_msg)[:120])

    if match.entry.success_count <= 1:
        reg.revoke_label(match.entry.label_id)
    else:
        reg.demote_label(match.entry.label_id)
    misses = int(state.get("scene_label_misses") or state.get("scene_memory_misses") or 0) + 1
    state["scene_label_misses"] = misses
    state["scene_memory_misses"] = misses
    return SceneLabelFastPathResult(handled=False, message="label_fast_path_no_progress")


def learn_scene_label_after_step(
    state: LaunchGraphState,
    *,
    artifact_root: Path,
    before_ocr: str,
    after_ocr: str,
    bboxes: list[OcrBbox],
    step,
    round_id: int,
    screenshot_ref: str,
    screen_w: int,
    screen_h: int,
    screen_analysis=None,
    step_passed: bool,
    scope: SceneLabelScope = "in_game",
    cfg: SceneLabelsSection | None = None,
) -> None:
    if not step_passed or step.action != "tap_xy":
        return
    reg = _registry(artifact_root, cfg)
    judgment_raw = state.get("last_scene_label_judgment")
    judgment = None
    if isinstance(judgment_raw, dict) and judgment_raw:
        try:
            judgment = SceneLabelJudgment.model_validate(judgment_raw)
        except Exception:
            judgment = None
    slug = str(state.get("scene_label_slug") or "")
    strategy = str(state.get("scene_label_coord_strategy") or "none")
    target = str(state.get("scene_label_semantic_target") or "")
    entry = reg.learn_from_verified_step(
        judgment=judgment,
        label_slug=slug,
        coord_strategy=strategy,  # type: ignore[arg-type]
        semantic_target=target,
        ocr_summary=before_ocr,
        after_ocr=after_ocr,
        bboxes=bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
        step=step,
        round_id=round_id,
        screenshot_ref=screenshot_ref,
        screen_analysis=screen_analysis,
        scope=scope,
        source="slow_path",
    )
    if entry is not None:
        learns = int(state.get("scene_label_learns") or state.get("scene_memory_learns") or 0) + 1
        state["scene_label_learns"] = learns
        state["scene_memory_learns"] = learns
        state["last_scene_memory_learned"] = {
            "memory_id": entry.label_id,
            "archetype": entry.label_slug,
        }
