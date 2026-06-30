"""scene_label_executor 单元测试。"""

from __future__ import annotations

from game_agent.models.scene import SceneTransition
from game_agent.models.scene_label import SceneLabelEntry, SceneLabelJudgment
from game_agent.services.scene_label_executor import (
    plan_from_scene_label,
    should_run_pulse_for_label,
)
from game_agent.utils.ocr_util import OcrBbox


def test_should_run_pulse_for_label() -> None:
    j = SceneLabelJudgment(label_slug="x", coord_strategy="pulse", semantic_target="战斗")
    assert should_run_pulse_for_label(judgment=j, matched_entry=None) is True
    e = SceneLabelEntry(
        label_id="a",
        label_slug="b",
        coord_strategy="ocr",
        semantic_target="战斗",
    )
    assert should_run_pulse_for_label(judgment=None, matched_entry=e) is False


def test_pulse_plan_uses_ocr_target_not_dialogue_bbox() -> None:
    bboxes = [
        OcrBbox(text="好，开始战斗！", x1=100, y1=2000, x2=500, y2=2100, cx=300, cy=2050),
        OcrBbox(text="战斗", x1=500, y1=2600, x2=700, y2=2700, cx=600, cy=2650),
    ]
    judgment = SceneLabelJudgment(
        label_slug="pre_battle_deploy_tutorial_battle_cta",
        coord_strategy="pulse",
        semantic_target="战斗",
        confidence=0.9,
    )
    plan = plan_from_scene_label(
        judgment=judgment,
        matched_entry=None,
        bboxes=bboxes,
        ocr_summary="好，开始战斗！\n战斗",
        screen_w=1080,
        screen_h=2800,
        transition=SceneTransition(),
        motion_result=None,
        legacy_scene_id="dialogue",
    )
    assert plan.action == "tap_xy"
    assert plan.x == 600
    assert plan.y == 2650
    assert "战斗" in plan.reason


def test_ocr_strategy_strict_target() -> None:
    bboxes = [
        OcrBbox(text="战", x1=100, y1=2600, x2=150, y2=2650, cx=125, cy=2625),
        OcrBbox(text="战斗", x1=500, y1=2600, x2=700, y2=2700, cx=600, cy=2650),
    ]
    judgment = SceneLabelJudgment(
        label_slug="battle_cta",
        coord_strategy="ocr",
        semantic_target="战斗",
    )
    plan = plan_from_scene_label(
        judgment=judgment,
        matched_entry=None,
        bboxes=bboxes,
        ocr_summary="战\n战斗",
        screen_w=1080,
        screen_h=2800,
        transition=SceneTransition(),
        legacy_scene_id="tutorial",
    )
    assert plan.x == 600
    assert plan.target_text == "战斗"
