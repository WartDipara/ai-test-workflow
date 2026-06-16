"""plan_route 在 classify 节点写入 planned_next_route。"""

from __future__ import annotations

from game_agent.graphs.launch_routing import consume_planned_route, plan_route
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state


def test_plan_route_sets_planned_next_route() -> None:
    state = empty_launch_graph_state()
    state["facts"] = LaunchFacts(
        login_blocking=True,
        login_stage="login_form",
    ).model_dump()
    target = plan_route(state)
    assert target == "atomic_login"
    assert state.get("planned_next_route") == "atomic_login"
    assert state.get("last_route") == "atomic_login"
    assert state.get("current_tree_node") == "atomic_login"


def test_consume_planned_route_pops_once() -> None:
    state = empty_launch_graph_state()
    state["planned_next_route"] = "atomic_login"
    assert consume_planned_route(state) == "atomic_login"
    assert "planned_next_route" not in state
