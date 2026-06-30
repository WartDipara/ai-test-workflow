"""TapCoordResolver 单测。"""

from __future__ import annotations

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.services.behavior_chain import BehaviorStep, behavior_step_from_vlm_analysis
from game_agent.services.tap_coord_resolver import infer_coord_source, resolve_step_coordinates
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, *, cx: int, cy: int) -> OcrBbox:
    return OcrBbox(text=text, x1=cx - 10, y1=cy - 10, x2=cx + 10, y2=cy + 10, cx=cx, cy=cy)


def test_vlm_fused_step_keeps_coordinates() -> None:
    analysis = InGameScreenAnalysis(
        recommended_action="tap_text",
        tap_target_text="战",
        semantic_target_text="战斗",
        target_has_ocr_semantics=True,
        tap_x=554,
        tap_y=2328,
        tap_source="motion_ocr_fused",
        tap_confidence=0.97,
        confidence=0.95,
        recommended_coord_source="vlm_xy",
        fusion_reason="battle CTA",
    )
    bboxes = [
        _bbox("助战", cx=243, cy=345),
        _bbox("战斗", cx=554, cy=2328),
    ]
    step = behavior_step_from_vlm_analysis(
        analysis, bboxes=bboxes, screen_w=1080, screen_h=2400
    )
    assert step is not None
    assert step.x == 554
    assert step.y == 2328


def test_resolve_ocr_uses_semantic_target() -> None:
    analysis = InGameScreenAnalysis(
        semantic_target_text="战斗",
        target_has_ocr_semantics=True,
        recommended_coord_source="ocr",
        tap_x=554,
        tap_y=2328,
    )
    bboxes = [
        _bbox("助战", cx=243, cy=345),
        _bbox("战斗", cx=554, cy=2328),
    ]
    step = BehaviorStep(
        id="s1",
        action="tap_text",
        target_text="战",
    )
    resolved = resolve_step_coordinates(
        step,
        screen_analysis=analysis,
        bboxes=bboxes,
        motion_result=None,
        screen_w=1080,
        screen_h=2400,
    )
    assert resolved.x == 554
    assert resolved.y == 2328


def test_infer_coord_source_from_analysis() -> None:
    analysis = InGameScreenAnalysis(
        target_has_ocr_semantics=True,
        recommended_coord_source="ocr",
    )
    step = BehaviorStep(id="s1", action="tap_xy", x=0, y=0)
    assert infer_coord_source(step, analysis) == "ocr"
