"""Scene gate VLM 裁决与合并测试。"""

from __future__ import annotations

from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.models.scene import SceneClassification
from game_agent.models.scene_gate import SceneGateJudgment
from game_agent.services.scene_gate import (
    merge_scene_gate_judgment,
    plan_from_scene_gate,
    scene_id_from_scene_gate,
    should_invoke_scene_gate_vlm,
)
from game_agent.utils.ocr_util import OcrBbox


def test_should_invoke_after_login_without_static_work() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    facts = LaunchFacts()
    rule = SceneClassification(scene_id="unknown", confidence=0.0)
    assert should_invoke_scene_gate_vlm(state, facts, rule_classification=rule) is True


def test_should_not_invoke_when_download_pending() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    facts = LaunchFacts(download_visible=True)
    rule = SceneClassification(scene_id="unknown", confidence=0.0)
    assert should_invoke_scene_gate_vlm(state, facts, rule_classification=rule) is False


def test_vlm_overrides_ocr_loading_with_dialogue() -> None:
    rule = SceneClassification(scene_id="loading", confidence=0.9, evidence="black", source="rule")
    judgment = SceneGateJudgment(
        scene_id="dialogue",
        confidence=0.88,
        description="Story cutscene with bottom dialogue box",
        action="tap_dialogue",
        reason="narrative text visible",
    )
    merged, _ = merge_scene_gate_judgment(
        rule,
        judgment,
        bboxes=[],
        ocr_summary="",
        screen_h=2400,
    )
    assert merged.scene_id == "dialogue"
    assert merged.source == "vlm"
    assert merged.confidence >= 0.55


def test_vlm_tap_dialogue_uses_ocr_coords_not_vlm() -> None:
    from game_agent.models.scene import SceneTransition
    from game_agent.services.scene_strategies import plan_scene_action

    state = empty_launch_graph_state()
    state["scene_gate_scene_id"] = "dialogue"
    state["scene_gate_confidence"] = 0.9
    state["scene_gate_action"] = "tap_dialogue"
    state["scene_gate_description"] = "NPC speech box"

    assert plan_from_scene_gate(state, scene_id="loading") is None
    assert scene_id_from_scene_gate(state, fallback="loading") == "dialogue"

    bboxes = [
        OcrBbox(
            text="这把剑?这似乎是……轩辕剑!",
            cx=313,
            cy=2028,
            x1=50,
            y1=1990,
            x2=900,
            y2=2060,
        ),
    ]
    plan = plan_scene_action(
        "dialogue",
        bboxes,
        ocr_summary="",
        screen_w=1080,
        screen_h=2400,
        transition=SceneTransition(kind="none"),
    )
    assert plan.action == "tap_xy"
    assert plan.reason == "dialogue:tap_narrative_box"
    assert plan.x == 313
    assert plan.y == 2028


def test_vlm_wait_on_loading() -> None:
    state = empty_launch_graph_state()
    state["scene_gate_scene_id"] = "loading"
    state["scene_gate_confidence"] = 0.9
    state["scene_gate_action"] = "wait"
    plan = plan_from_scene_gate(state, scene_id="loading")
    assert plan is not None
    assert plan.action == "wait"
    assert plan.reason == "scene_gate:vlm_wait"
