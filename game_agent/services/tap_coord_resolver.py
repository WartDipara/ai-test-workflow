"""局内行为步坐标解析：主脑选源后绑定 OCR / 脉冲 / VLM 坐标。"""

from __future__ import annotations

import logging
from pathlib import Path

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.motion_probe import MotionProbeResult
from game_agent.models.tutorial_pulse import TutorialPulsePick
from game_agent.services.behavior_chain import (
    BehaviorStep,
    CoordSourceType,
    bbox_for_text_strict,
    clamp_coord,
)
from game_agent.services.tutorial_pulse_locator import resolve_tutorial_visual_tap
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)


def infer_coord_source(
    step: BehaviorStep,
    analysis: InGameScreenAnalysis | None,
) -> CoordSourceType:
    if step.coord_source:
        return step.coord_source
    if analysis is None:
        if step.action == "tap_text":
            return "ocr"
        return ""
    rec = analysis.recommended_coord_source
    if rec and rec != "none":
        return rec  # type: ignore[return-value]
    if analysis.use_dim_region_tap or analysis.tap_source == "dialogue_blank":
        return "dialogue_blank"
    if analysis.tap_source in ("motion_pulse",) or (
        analysis.tap_source == "motion_ocr_fused"
        and not analysis.target_has_ocr_semantics
    ):
        return "pulse"
    if analysis.tap_source in ("motion_ocr_fused", "ocr_bbox") and analysis.tap_x > 0:
        return "vlm_xy" if analysis.tap_source == "motion_ocr_fused" else "ocr"
    if analysis.target_has_ocr_semantics or step.action == "tap_text":
        return "ocr"
    if analysis.tap_x > 0 and analysis.tap_y > 0:
        return "vlm_xy"
    return ""


def resolve_step_coordinates(
    step: BehaviorStep,
    *,
    screen_analysis: InGameScreenAnalysis | None,
    bboxes: list[OcrBbox],
    motion_result: MotionProbeResult | None,
    screen_w: int,
    screen_h: int,
    ocr_summary: str = "",
    shot_path: Path | None = None,
    vlm_pick: TutorialPulsePick | None = None,
) -> BehaviorStep:
    """按 coord_source 将语义目标绑定为可执行 tap_xy。"""
    if step.action not in ("tap_xy", "tap_text"):
        return step

    source = infer_coord_source(step, screen_analysis)
    cur = step.model_copy(deep=True)
    cur.coord_source = source

    prefer_xy: tuple[int, int] | None = None
    if screen_analysis is not None and screen_analysis.tap_x > 0 and screen_analysis.tap_y > 0:
        prefer_xy = (screen_analysis.tap_x, screen_analysis.tap_y)
    elif cur.x > 0 and cur.y > 0:
        prefer_xy = (cur.x, cur.y)

    if source == "dialogue_blank":
        from game_agent.services.dialogue_heuristics import dialogue_box_fallback_xy

        fx, fy = dialogue_box_fallback_xy(screen_w, screen_h)
        cur.action = "tap_xy"
        cur.x, cur.y = fx, fy
        return cur

    if source == "pulse":
        if motion_result is None:
            logger.warning(
                "[TapCoordResolver] pulse_requested_without_burst "
                "(no motion_result; skip static_glow fallback)"
            )
            return cur
        tap = resolve_tutorial_visual_tap(
            motion=motion_result,
            screenshot_path=shot_path,
            screen_w=screen_w,
            screen_h=screen_h,
            ocr_summary=ocr_summary,
            bboxes=bboxes,
            vlm_pick=vlm_pick,
            allow_static_glow=False,
        )
        if tap is not None:
            cur.action = "tap_xy"
            cur.x = clamp_coord(tap.x, screen_w)
            cur.y = clamp_coord(tap.y, screen_h)
            cur.reason = (cur.reason or "")[:200] + f" [{tap.reason}]"
            logger.info(
                "[TapCoordResolver] pulse (%d,%d) | %s",
                cur.x,
                cur.y,
                tap.reason[:80],
            )
        return cur

    if source == "vlm_xy":
        if prefer_xy is not None:
            cur.action = "tap_xy"
            cur.x = clamp_coord(prefer_xy[0], screen_w)
            cur.y = clamp_coord(prefer_xy[1], screen_h)
            logger.info("[TapCoordResolver] vlm_xy (%d,%d)", cur.x, cur.y)
        return cur

    if source == "ocr" or cur.action == "tap_text":
        target = (cur.target_text or "").strip()
        if not target and screen_analysis is not None:
            target = (
                screen_analysis.semantic_target_text
                or screen_analysis.tap_target_text
                or ""
            ).strip()
        if target:
            bbox = bbox_for_text_strict(bboxes, target, prefer_xy=prefer_xy)
            if bbox is not None:
                cur.action = "tap_xy"
                cur.x = clamp_coord(bbox.cx, screen_w)
                cur.y = clamp_coord(bbox.cy, screen_h)
                cur.target_text = target[:80]
                logger.info(
                    "[TapCoordResolver] ocr %r -> (%d,%d)",
                    target[:20],
                    cur.x,
                    cur.y,
                )
                return cur
        if prefer_xy is not None:
            cur.action = "tap_xy"
            cur.x = clamp_coord(prefer_xy[0], screen_w)
            cur.y = clamp_coord(prefer_xy[1], screen_h)
            cur.coord_source = "vlm_xy"
        return cur

    if cur.x > 0 and cur.y > 0:
        return cur

    return cur
