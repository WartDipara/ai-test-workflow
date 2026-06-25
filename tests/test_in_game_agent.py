"""in-game agent 动作解析、白名单与 deadline 逻辑测试。"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from game_agent.graphs.launch_routing import plan_route
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.services.in_game_agent import (
    InGameActionPlan,
    _parse_in_game_action_json,
    execute_in_game_action,
    sanitize_in_game_plan,
)
from game_agent.models.settings import GameSection
from game_agent.services.run_deliverable import build_in_game_play_summary
from game_agent.utils.ocr_util import OcrBbox


def test_parse_in_game_action_json_whitelist() -> None:
    raw = """
    {"action": "swipe", "x": 100, "y": 800, "x2": 100, "y2": 400,
     "wait_s": 2, "reason": "scroll map", "stage": "in_game"}
    """
    plan = _parse_in_game_action_json(raw, screen_w=1080, screen_h=2400, max_wait_s=5.0)
    assert plan is not None
    assert plan.action == "swipe"
    assert plan.x == 100
    assert plan.y2 == 400


def test_parse_rejects_unknown_action() -> None:
    raw = '{"action": "install_apk", "x": 1, "y": 1}'
    plan = _parse_in_game_action_json(raw, screen_w=1080, screen_h=2400, max_wait_s=5.0)
    assert plan is not None
    assert plan.action == "none"


def test_sanitize_dedupes_repeat_tap() -> None:
    plan = InGameActionPlan(action="tap_xy", x=500, y=900, reason="tap quest")
    sig = plan.signature()
    out = sanitize_in_game_plan(
        plan,
        bboxes=[],
        screen_w=1080,
        screen_h=2400,
        prior_signature=sig,
        same_action_streak=2,
        max_same_action=2,
    )
    assert out.action == "wait"


def test_execute_swipe_calls_adb() -> None:
    adb = MagicMock()
    adb.swipe.return_value = "swiped"
    plan = InGameActionPlan(action="swipe", x=100, y=800, x2=100, y2=400)
    msg = execute_in_game_action(plan, adb=adb, sw=1080, sh=2400)
    assert msg == "swiped"
    adb.swipe.assert_called_once_with(100, 800, 100, 400, width=1080, height=2400)


def test_plan_route_in_game_agent_after_stability() -> None:
    state = empty_launch_graph_state()
    state.update(
        {
            "login_done": True,
            "privacy_checked": True,
            "server_checked": True,
            "enter_tapped_count": 1,
            "in_game_entry_passed": True,
            "stability_observe_complete": True,
            "in_game_agent_started_at": time.monotonic(),
            "in_game_agent_deadline": time.monotonic() + 1200.0,
            "facts": LaunchFacts(download_visible=True, enter_cta_visible=False).model_dump(),
        },
    )
    assert plan_route(state) == "in_game_agent"


def test_plan_route_not_download_when_in_game() -> None:
    state = empty_launch_graph_state()
    state.update(
        {
            "login_done": True,
            "privacy_checked": True,
            "enter_tapped_count": 1,
            "in_game_entry_passed": True,
            "stability_observe_complete": True,
            "in_game_agent_started_at": time.monotonic(),
            "in_game_agent_deadline": time.monotonic() + 600.0,
            "facts": LaunchFacts(download_visible=True).model_dump(),
        },
    )
    assert plan_route(state) != "handle_download"


def test_tap_text_resolves_from_bbox() -> None:
    bboxes = [OcrBbox(text="确定", cx=540, cy=1200, x1=0, y1=0, x2=0, y2=0)]
    plan = InGameActionPlan(action="tap_text", target_text="确定", reason="close dialog")
    out = sanitize_in_game_plan(
        plan,
        bboxes=bboxes,
        screen_w=1080,
        screen_h=2400,
    )
    assert out.action == "tap_xy"
    assert out.x == 540
    assert out.y == 1200


def test_build_in_game_play_summary_from_graph() -> None:
    summary = build_in_game_play_summary(
        {
            "in_game_play_completed": True,
            "in_game_mode": "smoke",
            "in_game_play_duration_s": 180,
            "in_game_play_rounds": 12,
            "in_game_play_chains_built": 3,
            "in_game_play_steps_executed": 28,
            "in_game_behavior_replan_count": 1,
        },
    )
    assert summary is not None
    assert summary["mode"] == "smoke"
    assert summary["duration_s"] == 180
    assert summary["completed"] is True


def test_resolve_in_game_run_s_explicit_override() -> None:
    cfg = GameSection(in_game_run_s=600.0)
    assert cfg.resolve_in_game_run_s() == 600.0
