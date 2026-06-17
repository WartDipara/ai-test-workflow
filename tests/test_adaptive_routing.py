"""phase_planner 解析与 adaptive 路由纯逻辑测试。"""

from __future__ import annotations

from game_agent.graphs.launch_routing import plan_route, should_route_adaptive, should_route_free
from game_agent.graphs.launch_tree import launch_dfs_next
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.services.phase_planner import parse_phase_spec_raw


def test_parse_phase_spec_json() -> None:
    raw = """
    {
      "flow_active": true,
      "phase_id": "list_pick",
      "phase_label": "职业选择",
      "action": "tap_xy",
      "x": 140,
      "y": 620,
      "wait_s": 2.5,
      "complete": {"kind": "fingerprint_change", "hint": ""},
      "confidence": 0.9
    }
    """
    spec = parse_phase_spec_raw(raw)
    assert spec is not None
    assert spec.flow_active is True
    assert spec.phase_id == "list_pick"
    assert spec.action == "tap_xy"
    assert spec.complete.kind == "fingerprint_change"


def test_should_route_adaptive_when_check_in_game_failed() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["failed_nodes"] = {
        "enter.check_in_game": {"node": "enter.check_in_game", "failed": True, "attempts": 1},
    }
    facts = LaunchFacts()
    assert should_route_adaptive(state, facts) is True


def test_should_not_route_adaptive_when_flow_done() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["adaptive_flow_done"] = True
    facts = LaunchFacts()
    assert should_route_adaptive(state, facts) is False


def test_plan_route_adaptive_before_check_in_game() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["privacy_checked"] = True
    state["server_checked"] = True
    state["enter_tapped_count"] = 1
    state["failed_nodes"] = {
        "enter.check_in_game": {
            "node": "enter.check_in_game",
            "failed": True,
            "attempts": 1,
            "last_error": "not in game",
        },
    }
    state["facts"] = LaunchFacts(enter_cta_visible=False).model_dump()
    target = plan_route(state)
    assert target == "adaptive_phase"


def test_should_route_adaptive_when_active_node_id() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["adaptive_active_node_id"] = "adaptive.list_pick"
    facts = LaunchFacts()
    assert should_route_adaptive(state, facts) is True
    assert plan_route(state) == "adaptive_phase"


def test_should_route_free_false_when_adaptive_active() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["adaptive_active_node_id"] = "adaptive.list_pick"
    state["current_phase_spec"] = {
        "phase_id": "list_pick",
        "phase_label": "职业选择",
        "action": "tap_xy",
        "flow_active": True,
    }
    facts = LaunchFacts(character_creation_blocking=True)
    state["facts"] = facts.model_dump()
    decision = launch_dfs_next(state, facts)
    assert should_route_adaptive(state, facts) is True
    assert should_route_free(state, facts, decision) is False


def test_dfs_adaptive_before_check_in_game() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["privacy_checked"] = True
    state["server_checked"] = True
    state["enter_tapped_count"] = 1
    state["failed_nodes"] = {
        "enter.check_in_game": {"node": "enter.check_in_game", "failed": True, "attempts": 1},
    }
    facts = LaunchFacts(enter_cta_visible=False, character_creation_blocking=True)
    state["facts"] = facts.model_dump()
    decision = launch_dfs_next(state, facts)
    assert decision.action == "adaptive_phase"
    assert decision.node_id == "post_login.adaptive"
