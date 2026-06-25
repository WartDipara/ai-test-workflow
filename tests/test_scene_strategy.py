"""场景分类与策略路由纯逻辑测试。"""

from __future__ import annotations

from game_agent.graphs.launch_routing import plan_route, should_route_adaptive, should_route_scene
from game_agent.graphs.launch_state_store import mark_tree_node_done
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.models.scene import SceneClassification, SceneTransition
from game_agent.services.scene_classifier import classify_scene, detect_scene_transition
from game_agent.services.scene_strategies import (
    apply_scene_classification,
    clear_scene_strategy,
    is_pre_login_passive_wait,
    plan_dialogue_action,
    plan_scene_action,
    should_activate_scene_strategy,
    should_deactivate_scene_strategy,
)
from game_agent.utils.ocr_util import OcrBbox


def _dialogue_bboxes(screen_h: int = 2400) -> list[OcrBbox]:
    bottom_y = int(screen_h * 0.78)
    return [
        OcrBbox(
            text="这位少侠，江湖路远，且听我细细道来。",
            cx=540,
            cy=bottom_y,
            x1=100,
            y1=bottom_y - 20,
            x2=980,
            y2=bottom_y + 20,
        ),
        OcrBbox(
            text="点击继续",
            cx=900,
            cy=int(screen_h * 0.9),
            x1=820,
            y1=int(screen_h * 0.88),
            x2=980,
            y2=int(screen_h * 0.92),
        ),
    ]


def test_classify_dialogue_from_bottom_narrative() -> None:
    facts = LaunchFacts(login_blocking=False)
    bboxes = _dialogue_bboxes()
    ocr = "这位少侠，江湖路远，且听我细细道来。 点击继续"
    cls = classify_scene(
        facts,
        bboxes,
        ocr_summary=ocr,
        screen_w=1080,
        screen_h=2400,
    )
    assert cls.scene_id == "dialogue"
    assert cls.confidence >= 0.55
    assert "narrative" in cls.evidence or "bottom" in cls.evidence


def test_classify_narrative_dialogue_without_continue_cta() -> None:
    """新手剧情：底部角色名+台词，无「点击继续」字样也应识别为 dialogue。"""
    screen_h = 2400
    bboxes = [
        OcrBbox(text="无上宗寂灭", cx=854, cy=1917, x1=700, y1=1890, x2=1000, y2=1940),
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
    ocr = "无上宗寂灭 这把剑?这似乎是……轩辕剑!"
    facts = LaunchFacts(login_blocking=False)
    cls = classify_scene(
        facts,
        bboxes,
        ocr_summary=ocr,
        screen_w=1080,
        screen_h=screen_h,
    )
    assert cls.scene_id == "dialogue"
    assert cls.confidence >= 0.55
    plan = plan_scene_action(
        "dialogue",
        bboxes,
        ocr_summary=ocr,
        screen_w=1080,
        screen_h=screen_h,
        transition=SceneTransition(kind="none"),
    )
    assert plan.action == "tap_xy"
    assert plan.mode == "advance"
    assert plan.reason == "dialogue:tap_narrative_box"
    assert plan.x == 313
    assert plan.y == 2028


def test_loading_strategy_switches_to_dialogue_on_classify() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["scene_strategy_active"] = True
    state["active_scene_strategy"] = "loading"
    facts = LaunchFacts()
    cls = SceneClassification(
        scene_id="dialogue",
        confidence=0.77,
        evidence="dual_bottom_lines",
        fingerprint="dialogue|test",
    )
    apply_scene_classification(
        state,
        cls,
        SceneTransition(kind="none"),
        facts,
    )
    assert state.get("scene_strategy_active") is True
    assert state.get("active_scene_strategy") == "dialogue"


def test_no_continue_button_taps_narrative_line() -> None:
    bboxes = [
        OcrBbox(
            text="江湖险恶，多加小心。",
            cx=500,
            cy=2100,
            x1=80,
            y1=2060,
            x2=920,
            y2=2140,
        ),
    ]
    plan = plan_dialogue_action(
        bboxes,
        ocr_summary="江湖险恶，多加小心。",
        screen_w=1080,
        screen_h=2400,
        transition=SceneTransition(kind="none"),
    )
    assert plan.action == "tap_xy"
    assert plan.reason == "dialogue:tap_narrative_box"
    assert plan.y >= int(2400 * 0.5)


def test_plan_dialogue_uses_continue_when_no_narrative_line() -> None:
    bboxes = [
        OcrBbox(
            text="点击继续",
            cx=900,
            cy=int(2400 * 0.9),
            x1=820,
            y1=int(2400 * 0.88),
            x2=980,
            y2=int(2400 * 0.92),
        ),
    ]
    plan = plan_dialogue_action(
        bboxes,
        ocr_summary="点击继续",
        screen_w=1080,
        screen_h=2400,
        transition=SceneTransition(kind="none"),
    )
    assert plan.action == "tap_xy"
    assert plan.x == 900
    assert plan.target_text == "点击继续"


def test_plan_dialogue_taps_narrative_over_continue_label() -> None:
    bboxes = _dialogue_bboxes()
    plan = plan_dialogue_action(
        bboxes,
        ocr_summary="这位少侠 点击继续",
        screen_w=1080,
        screen_h=2400,
        transition=SceneTransition(kind="none"),
    )
    assert plan.action == "tap_xy"
    assert plan.reason == "dialogue:tap_narrative_box"
    assert plan.x == 540


def test_animation_transition_uses_wait_observe_not_exit() -> None:
    plan = plan_dialogue_action(
        _dialogue_bboxes(),
        ocr_summary="",
        screen_w=1080,
        screen_h=2400,
        transition=SceneTransition(kind="animation_or_loading", reason="black"),
    )
    assert plan.action == "wait"
    assert plan.mode == "wait_observe"

    state = empty_launch_graph_state()
    state["login_done"] = True
    state["scene_strategy_active"] = True
    state["active_scene_strategy"] = "dialogue"
    facts = LaunchFacts()
    transition = SceneTransition(kind="animation_or_loading", reason="black")
    cls = SceneClassification(scene_id="loading", confidence=0.9, evidence="black")
    assert should_deactivate_scene_strategy(state, cls, facts, transition) is False


def test_low_confidence_deactivates_after_streak_not_click_count() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["scene_strategy_active"] = True
    state["active_scene_strategy"] = "dialogue"
    facts = LaunchFacts()
    low_cls = SceneClassification(scene_id="unknown", confidence=0.1, evidence="none")
    transition = SceneTransition(kind="low_confidence", reason="cannot_confirm")

    assert should_deactivate_scene_strategy(state, low_cls, facts, transition) is False
    assert state.get("scene_low_confidence_streak") == 1
    assert should_deactivate_scene_strategy(state, low_cls, facts, transition) is True


def test_scene_changed_to_character_creation_deactivates() -> None:
    state = empty_launch_graph_state()
    state["scene_strategy_active"] = True
    state["active_scene_strategy"] = "dialogue"
    facts = LaunchFacts(character_creation_blocking=True)
    cls = classify_scene(
        facts,
        [OcrBbox(text="创建角色", cx=100, cy=100, x1=0, y1=0, x2=200, y2=200)],
        ocr_summary="创建角色 选择职业",
        screen_w=1080,
        screen_h=2400,
    )
    transition = SceneTransition(
        kind="scene_changed",
        from_scene="dialogue",
        to_scene="character_creation",
    )
    assert cls.scene_id == "character_creation"
    assert should_deactivate_scene_strategy(state, cls, facts, transition) is True


def test_should_route_scene_before_adaptive() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["scene_id"] = "dialogue"
    state["scene_confidence"] = 0.7
    state["scene_strategy_active"] = True
    state["active_scene_strategy"] = "dialogue"
    state["facts"] = LaunchFacts(scene_id="dialogue", scene_confidence=0.7).model_dump()

    facts = LaunchFacts(scene_id="dialogue", scene_confidence=0.7)
    assert should_route_scene(state, facts) is True
    assert should_route_adaptive(state, facts) is False
    assert plan_route(state) == "scene_action"


def test_pre_login_dialogue_routes_scene() -> None:
    state = empty_launch_graph_state()
    state["scene_id"] = "dialogue"
    state["scene_confidence"] = 0.7
    state["facts"] = LaunchFacts(scene_id="dialogue", scene_confidence=0.7).model_dump()
    facts = LaunchFacts(scene_id="dialogue", scene_confidence=0.7)
    assert should_route_scene(state, facts) is True
    assert plan_route(state) == "scene_action"


def test_apply_scene_classification_activates_dialogue() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    cls = SceneClassification(
        scene_id="dialogue",
        confidence=0.72,
        evidence="test",
        fingerprint="dialogue|点击继续",
    )
    apply_scene_classification(
        state,
        cls,
        SceneTransition(kind="none"),
        LaunchFacts(),
    )
    assert state["scene_strategy_active"] is True
    assert state["active_scene_strategy"] == "dialogue"


def test_clear_scene_strategy_resets_flags() -> None:
    state = empty_launch_graph_state()
    state["scene_strategy_active"] = True
    state["active_scene_strategy"] = "dialogue"
    state["scene_low_confidence_streak"] = 2
    clear_scene_strategy(state)
    assert state["scene_strategy_active"] is False
    assert state["active_scene_strategy"] == ""
    assert state["scene_low_confidence_streak"] == 0


def test_detect_scene_transition_exit_on_hud() -> None:
    facts = LaunchFacts()
    cls = SceneClassification(scene_id="in_game_hud", confidence=0.85)
    transition = detect_scene_transition(
        prev_scene_id="dialogue",
        prev_fingerprint="dialogue|old",
        classification=cls,
        facts=facts,
        ocr_summary="背包 技能 商城",
    )
    assert transition.kind == "exit_to_game"


def test_loading_strategy_waits() -> None:
    plan = plan_scene_action(
        "loading",
        [],
        ocr_summary="",
        screen_w=1080,
        screen_h=2400,
        transition=SceneTransition(kind="none"),
    )
    assert plan.action == "wait"
    assert plan.mode == "wait_observe"


def test_loading_active_before_login() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = False
    facts = LaunchFacts()
    cls = SceneClassification(scene_id="loading", confidence=0.7, evidence="loading_text")
    assert should_activate_scene_strategy(state, cls, facts) is True
    state["scene_id"] = "loading"
    state["scene_confidence"] = 0.7
    state["facts"] = facts.model_dump()
    assert should_route_scene(state, facts) is True
    assert plan_route(state) == "scene_action"


def test_dialogue_active_before_login_when_no_login_form() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = False
    facts = LaunchFacts()
    cls = SceneClassification(scene_id="dialogue", confidence=0.72, evidence="narrative")
    assert should_activate_scene_strategy(state, cls, facts) is True


def test_pre_login_unknown_after_privacy_waits_not_recover() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = False
    mark_tree_node_done(state, "handle_initial_privacy_dialog")
    facts = LaunchFacts()
    state["scene_id"] = "unknown"
    state["scene_confidence"] = 0.0
    state["facts"] = facts.model_dump()
    assert is_pre_login_passive_wait(state, facts, scene_id="unknown", confidence=0.0) is True
    assert should_route_scene(state, facts) is True
    assert plan_route(state) == "scene_action"
