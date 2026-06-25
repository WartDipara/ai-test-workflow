"""通用行为链：不绑定选角，可用于战斗、弹窗、地图移动等场景。"""

from __future__ import annotations

from unittest.mock import MagicMock

from game_agent.models.launch_graph_state import empty_launch_graph_state
from game_agent.services.behavior_chain import (
    BehaviorStep,
    evaluate_step_success,
    execute_behavior_step,
    parse_behavior_chain_json,
    record_behavior_chain_failure,
    sanitize_press_back_step,
    should_downgrade_press_back,
    validate_behavior_chain,
)
from game_agent.services.in_game_agent import (
    advance_in_game_behavior_cursor,
    get_current_in_game_behavior_step,
    set_in_game_behavior_chain,
)
from game_agent.utils.ocr_util import OcrBbox


def test_parse_generic_combat_behavior_chain() -> None:
    raw = """
    {
      "source": "vision",
      "stage": "in_game",
      "goal": "advance a combat encounter",
      "steps": [
        {
          "id": "target_enemy",
          "action": "tap_xy",
          "x": 820,
          "y": 720,
          "intent": "target the visible enemy",
          "success_criteria": ["enemy target marker appears"],
          "reason": "enemy is visible on the right"
        },
        {
          "id": "use_skill",
          "action": "tap_text",
          "target_text": "技能",
          "intent": "use an available combat skill",
          "success_criteria": ["skill cooldown starts", "damage number appears"]
        },
        {
          "id": "kite",
          "action": "swipe",
          "x": 300,
          "y": 1600,
          "x2": 520,
          "y2": 1450,
          "intent": "move away from danger",
          "success_criteria": ["character position changes"]
        }
      ]
    }
    """
    bboxes = [OcrBbox(text="技能", cx=930, cy=2100, x1=0, y1=0, x2=0, y2=0)]

    chain = parse_behavior_chain_json(raw, screen_w=1080, screen_h=2400)
    assert chain is not None
    validated = validate_behavior_chain(chain, bboxes=bboxes, screen_w=1080, screen_h=2400)
    assert validated is not None
    assert [step.action for step in validated.steps] == ["tap_xy", "tap_xy", "swipe"]
    assert validated.steps[1].x == 930
    assert validated.steps[2].intent == "move away from danger"


def test_execute_generic_swipe_step() -> None:
    adb = MagicMock()
    adb.swipe.return_value = "swiped"
    step = BehaviorStep(
        id="kite",
        action="swipe",
        x=300,
        y=1600,
        x2=520,
        y2=1450,
        intent="move away from danger",
    )

    assert execute_behavior_step(step, adb=adb, sw=1080, sh=2400) == "swiped"
    adb.swipe.assert_called_once_with(300, 1600, 520, 1450, width=1080, height=2400)


def test_in_game_behavior_chain_state_advances() -> None:
    state = empty_launch_graph_state()
    chain = parse_behavior_chain_json(
        """
        {
          "goal": "clear popup then observe",
          "steps": [
            {"id": "close", "action": "tap_xy", "x": 900, "y": 300, "intent": "close popup"},
            {"id": "wait", "action": "wait", "wait_s": 1.0, "intent": "wait for HUD"}
          ]
        }
        """,
        screen_w=1080,
        screen_h=2400,
    )
    assert chain is not None

    set_in_game_behavior_chain(state, chain)
    first = get_current_in_game_behavior_step(state)
    assert first is not None
    assert first.id == "close"
    assert advance_in_game_behavior_cursor(state) is True
    second = get_current_in_game_behavior_step(state)
    assert second is not None
    assert second.id == "wait"


def test_record_generic_failure_trace_with_prefix() -> None:
    state = empty_launch_graph_state()
    step = BehaviorStep(
        id="use_skill",
        action="tap_text",
        target_text="技能",
        intent="use combat skill",
        attempts=2,
    )

    trace = record_behavior_chain_failure(
        state,
        step,
        prefix="in_game_behavior",
        reason="skill button did not change state",
        ocr_summary="技能 普攻",
        artifact="shot.png",
    )

    assert trace.step_id == "use_skill"
    assert state["in_game_behavior_replan_count"] == 1
    assert state["in_game_behavior_last_failed_step_id"] == "use_skill"
    assert state["in_game_behavior_failure_trace"][0]["intent"] == "use combat skill"


def test_evaluate_step_success_keyword_appears() -> None:
    step = BehaviorStep(
        id="claim",
        action="tap_xy",
        x=1,
        y=1,
        success_criteria=["offline reward claimed"],
    )
    ok, reason = evaluate_step_success(
        step,
        before_ocr="Claim",
        after_ocr="offline reward claimed thanks",
    )
    assert ok
    assert "appeared" in reason


def test_evaluate_step_success_keyword_disappears() -> None:
    step = BehaviorStep(
        id="close",
        action="tap_xy",
        x=1,
        y=1,
        success_criteria=["!Claim"],
    )
    ok, _ = evaluate_step_success(
        step,
        before_ocr="Claim reward",
        after_ocr="main hud",
    )
    assert ok


def test_press_back_downgraded_on_exit_dialog() -> None:
    step = BehaviorStep(id="back", action="press_back", intent="close menu")
    out = sanitize_press_back_step(step, ocr_summary="Friendly Reminder Exit")
    assert out.action == "wait"
    assert should_downgrade_press_back("确认退出游戏")

