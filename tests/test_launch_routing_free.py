"""free / adaptive 路由：登录后创角/选角场景。"""

from __future__ import annotations

from game_agent.graphs.launch_routing import plan_route
from game_agent.graphs.launch_tree import launch_dfs_next
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state


def test_plan_route_adaptive_when_character_creation_blocking() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["privacy_checked"] = True
    state["server_checked"] = True
    state["facts"] = LaunchFacts(
        character_creation_blocking=True,
        enter_cta_visible=True,
        enter_cta_xy=(400, 1800),
    ).model_dump()
    target = plan_route(state)
    assert target == "adaptive_phase"
    assert state.get("planned_next_route") == "adaptive_phase"


def test_plan_route_adaptive_before_check_in_game_when_failed() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["privacy_checked"] = True
    state["server_checked"] = True
    state["enter_tapped_count"] = 1
    state["failed_nodes"] = {
        "enter.check_in_game": {
            "node": "enter.check_in_game",
            "done": False,
            "failed": True,
            "attempts": 1,
            "last_error": "not in game",
        },
    }
    state["facts"] = LaunchFacts(
        enter_cta_visible=False,
        character_creation_blocking=False,
    ).model_dump()
    target = plan_route(state)
    assert target == "adaptive_phase"


def test_plan_route_stability_observe_when_entry_passed() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["privacy_checked"] = True
    state["server_checked"] = True
    state["enter_tapped_count"] = 1
    state["in_game_entry_passed"] = True
    state["in_game_confirmed"] = False
    state["facts"] = LaunchFacts(
        enter_cta_visible=False,
        character_creation_blocking=False,
    ).model_dump()
    target = plan_route(state)
    assert target == "stability_observe"


def test_plan_route_not_free_when_entry_passed() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["privacy_checked"] = True
    state["enter_tapped_count"] = 1
    state["in_game_entry_passed"] = True
    state["failed_nodes"] = {
        "enter.check_in_game": {
            "node": "enter.check_in_game",
            "done": False,
            "failed": True,
            "attempts": 1,
            "last_error": "not in game",
        },
    }
    state["facts"] = LaunchFacts(character_creation_blocking=True).model_dump()
    target = plan_route(state)
    assert target == "stability_observe"


def test_plan_route_not_free_when_false_login_done_on_login_form() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["privacy_checked"] = True
    state["facts"] = LaunchFacts(
        login_blocking=True,
        login_stage="login_form",
        character_creation_blocking=True,
    ).model_dump()
    target = plan_route(state)
    assert target == "atomic_login"


def test_plan_route_not_free_before_login() -> None:
    state = empty_launch_graph_state()
    state["facts"] = LaunchFacts(
        login_blocking=True,
        login_stage="login_form",
        character_creation_blocking=True,
    ).model_dump()
    target = plan_route(state)
    assert target == "atomic_login"


def test_dfs_routes_adaptive_when_character_creation() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["privacy_checked"] = True
    state["server_checked"] = True
    facts = LaunchFacts(
        character_creation_blocking=True,
        enter_cta_visible=True,
        enter_cta_xy=(400, 1800),
    )
    state["facts"] = facts.model_dump()
    decision = launch_dfs_next(state, facts)
    assert decision.action == "adaptive_phase"
    assert decision.node_id == "post_login.adaptive"
