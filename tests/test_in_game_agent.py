"""in-game agent 路由与交付摘要测试。"""

from __future__ import annotations

import time

from game_agent.graphs.launch_routing import plan_route
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.models.settings import GameSection
from game_agent.services.run_deliverable import build_in_game_play_summary


def test_plan_route_in_game_agent_after_stability() -> None:
    state = empty_launch_graph_state()
    state.update(
        {
            "login_done": True,
            "privacy_checked": True,
            "server_checked": True,
            "enter_tapped_count": 1,
            "session_agent_active": True,
            "session_agent_started_at": time.monotonic(),
            "in_game_entry_passed": True,
            "stability_observe_complete": True,
            "in_game_agent_started_at": time.monotonic(),
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
            "session_agent_active": True,
            "session_agent_started_at": time.monotonic(),
            "in_game_entry_passed": True,
            "stability_observe_complete": True,
            "in_game_agent_started_at": time.monotonic(),
            "facts": LaunchFacts(download_visible=True).model_dump(),
        },
    )
    assert plan_route(state) != "handle_download"


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


def test_game_section_brain_defaults() -> None:
    g = GameSection()
    assert g.in_game_success_confirm_rounds == 2
    assert g.in_game_fail_confirm_rounds == 2
