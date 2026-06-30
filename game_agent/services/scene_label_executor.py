"""按 scene label coord_strategy 解析并生成 SceneActionPlan。"""

from __future__ import annotations

import logging
from pathlib import Path

from game_agent.models.motion_probe import MotionProbeResult, MotionProbeSection
from game_agent.models.scene import SceneActionPlan, SceneTransition
from game_agent.models.scene_label import CoordStrategy, SceneLabelEntry, SceneLabelJudgment
from game_agent.services.behavior_chain import bbox_for_text_strict
from game_agent.services.scene_memory_playbook import resolve_memory_action
from game_agent.services.tutorial_pulse_locator import resolve_tutorial_visual_tap
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)


def plan_from_scene_label(
    *,
    judgment: SceneLabelJudgment | None,
    matched_entry: SceneLabelEntry | None,
    bboxes: list[OcrBbox],
    ocr_summary: str,
    screen_w: int,
    screen_h: int,
    transition: SceneTransition,
    screenshot_path: Path | str | None = None,
    motion_result: MotionProbeResult | None = None,
    dim_tap_xy: tuple[int, int] | None = None,
    legacy_scene_id: str = "unknown",
) -> SceneActionPlan:
    """按 label coord_strategy 生成计划；pulse 禁止走 dialogue bbox。"""
    strategy: CoordStrategy = "none"
    target = ""
    if matched_entry is not None:
        strategy = matched_entry.coord_strategy
        target = matched_entry.semantic_target
    elif judgment is not None:
        strategy = judgment.normalized_coord_strategy()
        target = (judgment.semantic_target or "").strip()

    if strategy == "wait":
        return SceneActionPlan(
            action="wait",
            wait_s=2.5,
            reason=f"scene_label:wait:{matched_entry.label_slug if matched_entry else judgment.normalized_slug() if judgment else 'unknown'}",
            mode="wait_observe",
        )

    if strategy == "dim_region":
        if dim_tap_xy is not None:
            x, y = dim_tap_xy
            return SceneActionPlan(
                action="tap_xy",
                x=x,
                y=y,
                reason="scene_label:dim_region",
                mode="dim_advance",
            )
        if judgment is not None and judgment.use_dim_region_tap:
            from game_agent.services.scene_strategies import plan_dialogue_action

            return plan_dialogue_action(
                bboxes,
                ocr_summary=ocr_summary,
                screen_w=screen_w,
                screen_h=screen_h,
                transition=transition,
                advance_mode="dim_region",
                dim_tap_xy=dim_tap_xy,
            )

    if strategy == "pulse":
        shot = Path(screenshot_path) if screenshot_path else None
        tap = resolve_tutorial_visual_tap(
            motion=motion_result,
            screenshot_path=shot if shot and shot.is_file() else None,
            screen_w=screen_w,
            screen_h=screen_h,
            ocr_summary=ocr_summary,
            bboxes=bboxes,
            allow_static_glow=True,
        )
        if tap is not None:
            slug = matched_entry.label_slug if matched_entry else (
                judgment.normalized_slug() if judgment else "pulse"
            )
            return SceneActionPlan(
                action="tap_xy",
                x=tap.x,
                y=tap.y,
                reason=f"scene_label:pulse:{slug}:{tap.reason[:60]}",
                mode="advance",
            )
        if target:
            bbox = bbox_for_text_strict(bboxes, target)
            if bbox is not None:
                return SceneActionPlan(
                    action="tap_xy",
                    x=bbox.cx,
                    y=bbox.cy,
                    target_text=target,
                    reason=f"scene_label:pulse_fallback_ocr:{target}",
                    mode="advance",
                )
        miss_slug = matched_entry.label_slug if matched_entry else (
            judgment.normalized_slug() if judgment else "?"
        )
        logger.warning("[SceneLabel] pulse plan miss slug=%s target=%r", miss_slug, target)
        return SceneActionPlan(action="none", reason="scene_label:pulse_unresolved")

    if strategy == "ocr" and target:
        bbox = bbox_for_text_strict(bboxes, target)
        if bbox is not None:
            return SceneActionPlan(
                action="tap_xy",
                x=bbox.cx,
                y=bbox.cy,
                target_text=target,
                reason=f"scene_label:ocr:{target}",
                mode="advance",
            )

    if matched_entry is not None:
        xy = resolve_memory_action(
            matched_entry.execution_policy,
            bboxes=bboxes,
            screen_w=screen_w,
            screen_h=screen_h,
            dim_xy=dim_tap_xy,
        )
        if xy is not None:
            x, y = xy
            return SceneActionPlan(
                action="tap_xy",
                x=x,
                y=y,
                reason=f"scene_label:memory:{matched_entry.label_slug}",
                mode="advance",
            )

    scene_id = legacy_scene_id
    if judgment is not None:
        scene_id = judgment.legacy_scene_id()
    if strategy == "pulse":
        return SceneActionPlan(action="none", reason="scene_label:pulse_no_dialogue_fallback")
    from game_agent.services.scene_strategies import plan_scene_action

    return plan_scene_action(
        scene_id,
        bboxes,
        ocr_summary=ocr_summary,
        screen_w=screen_w,
        screen_h=screen_h,
        transition=transition,
        advance_mode="dim_region" if dim_tap_xy else "ocr",
        dim_tap_xy=dim_tap_xy,
        screenshot_path=screenshot_path,
        motion_result=motion_result,
    )


def should_run_pulse_for_label(
    *,
    judgment: SceneLabelJudgment | None,
    matched_entry: SceneLabelEntry | None,
) -> bool:
    if matched_entry is not None and matched_entry.coord_strategy == "pulse":
        return True
    if judgment is not None and judgment.normalized_coord_strategy() == "pulse":
        return True
    return False


def motion_cfg_for_scene_label_pulse(motion_cfg: MotionProbeSection) -> MotionProbeSection:
    """scene_action 路径上 pulse 策略强制启用连拍。"""
    return motion_cfg.model_copy(update={"always_burst": True})
