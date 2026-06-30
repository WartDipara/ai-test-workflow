"""Session Agent 路由与阶段边界测试。"""

from __future__ import annotations

import time

from game_agent.graphs.launch_routing import (
    plan_route,
    should_route_scene,
    should_route_session_agent,
)
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.services.session_agent import activate_session_agent


def test_activate_session_agent_on_enter_tapped() -> None:
    state = empty_launch_graph_state()
    state["enter_tapped_count"] = 1
    assert activate_session_agent(state) is True
    assert state["session_agent_active"] is True
    assert state["session_agent_started_at"] > 0
    assert state["scene_strategy_active"] is False


def test_should_route_session_agent_over_scene() -> None:
    state = empty_launch_graph_state()
    facts = LaunchFacts(
        login_done=True,
        scene_id="tutorial",
        scene_confidence=0.9,
    ).model_dump()
    state.update(
        {
            "login_done": True,
            "session_agent_active": True,
            "session_agent_started_at": time.monotonic(),
            "scene_strategy_active": True,
            "active_scene_strategy": "tutorial",
            "scene_id": "tutorial",
            "scene_confidence": 0.9,
            "facts": facts,
        },
    )
    f = LaunchFacts.model_validate(facts)
    assert should_route_scene(state, f) is False
    assert should_route_session_agent(state, f) is True
    assert plan_route(state) == "in_game_agent"


def test_plan_route_session_agent_without_stability() -> None:
    state = empty_launch_graph_state()
    state.update(
        {
            "login_done": True,
            "enter_tapped_count": 1,
            "session_agent_active": True,
            "session_agent_started_at": time.monotonic(),
            "facts": LaunchFacts(download_visible=False, enter_cta_visible=False).model_dump(),
        },
    )
    assert plan_route(state) == "in_game_agent"


def test_legacy_stability_path_still_routes_in_game_agent() -> None:
    state = empty_launch_graph_state()
    state.update(
        {
            "login_done": True,
            "enter_tapped_count": 1,
            "in_game_entry_passed": True,
            "stability_observe_complete": True,
            "session_agent_active": True,
            "session_agent_started_at": time.monotonic(),
            "in_game_agent_started_at": time.monotonic(),
            "facts": LaunchFacts(download_visible=True).model_dump(),
        },
    )
    assert plan_route(state) == "in_game_agent"
