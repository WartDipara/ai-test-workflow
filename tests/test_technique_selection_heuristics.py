"""技牌选择与暗色点击门控单测。"""

from __future__ import annotations

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.launch_graph_state import empty_launch_graph_state
from game_agent.services.behavior_chain import (
    BehaviorChain,
    BehaviorStep,
    apply_dim_region_to_chain,
)
from game_agent.services.scene_classifier import classify_scene
from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.services.technique_selection_heuristics import (
    is_technique_selection_screen,
    should_allow_dim_region_in_game,
    should_skip_dim_region_tap,
)
from game_agent.utils.ocr_util import OcrBbox


_TECHNIQUE_OCR = (
    "Technique Selection StormExtension Sandstorm Duration+100% "
    "Ice Spike DMG Inc Above Cangfeng Canyon Level 10"
)


def test_is_technique_selection_from_ocr() -> None:
    assert is_technique_selection_screen(_TECHNIQUE_OCR)


def test_is_technique_selection_from_vlm_signals() -> None:
    analysis = InGameScreenAnalysis(
        guidance_signals=["technique_selection_modal"],
        ui_stage="tutorial",
    )
    assert is_technique_selection_screen("", screen_analysis=analysis)


def test_should_skip_dim_on_technique_with_confident_vlm_tap() -> None:
    analysis = InGameScreenAnalysis(
        ui_stage="tutorial",
        guidance_signals=["technique_selection_modal"],
        recommended_action="tap_xy",
        tap_x=540,
        tap_y=954,
        tap_confidence=0.85,
        use_dim_region_tap=False,
    )
    assert should_skip_dim_region_tap(ocr_summary=_TECHNIQUE_OCR, screen_analysis=analysis)


def test_should_not_allow_dim_when_only_scene_dialogue_latched() -> None:
    state = empty_launch_graph_state()
    state["dialogue_advance_mode"] = "dim_region"
    state["scene_id"] = "dialogue"
    analysis = InGameScreenAnalysis(
        ui_stage="tutorial",
        guidance_signals=["technique_selection_modal"],
        recommended_action="tap_xy",
        tap_x=540,
        tap_y=954,
        tap_confidence=0.85,
        use_dim_region_tap=False,
    )
    assert not should_allow_dim_region_in_game(
        ui_stage="tutorial",
        ocr_summary=_TECHNIQUE_OCR,
        screen_analysis=analysis,
        state=state,
    )


def test_should_allow_dim_on_dialog_stage() -> None:
    state = empty_launch_graph_state()
    analysis = InGameScreenAnalysis(ui_stage="dialog", use_dim_region_tap=False)
    assert should_allow_dim_region_in_game(
        ui_stage="dialog",
        ocr_summary="角色台词很长的一段剧情对话",
        screen_analysis=analysis,
        state=state,
    )


def test_apply_dim_region_does_not_replace_vlm_fusion_tap() -> None:
    chain = BehaviorChain(
        steps=[
            BehaviorStep(
                id="vlm_fusion_tap",
                action="tap_xy",
                x=540,
                y=954,
                intent="tap center card",
            ),
            BehaviorStep(id="observe", action="wait", wait_s=1.5),
        ],
        source="brain",
        goal="pick technique",
    )
    updated = apply_dim_region_to_chain(chain, (571, 2096))
    assert updated is not None
    assert updated.steps[0].id == "vlm_fusion_tap"
    assert updated.steps[0].x == 540
    assert updated.source == "brain"


def test_classify_technique_selection_as_tutorial_not_dialogue() -> None:
    bboxes = [
        OcrBbox(text="Technique", x1=480, y1=690, x2=600, y2=720, cx=540, cy=705),
        OcrBbox(text="Selection", x1=480, y1=740, x2=600, y2=770, cx=540, cy=755),
        OcrBbox(text="Sandstorm", x1=480, y1=930, x2=600, y2=960, cx=540, cy=945),
    ]
    cls = classify_scene(
        LaunchFacts(),
        bboxes,
        ocr_summary=_TECHNIQUE_OCR,
        screen_h=2400,
    )
    assert cls.scene_id == "tutorial"
    assert "technique" in cls.evidence.lower()
