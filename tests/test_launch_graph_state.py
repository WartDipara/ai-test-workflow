from __future__ import annotations

from game_agent.models.launch_graph_state import (
    LaunchFacts,
    LaunchNodeStatus,
    empty_launch_graph_state,
    mark_node_done,
    mark_node_failed,
    node_attempts,
)


def test_empty_launch_graph_state_defaults() -> None:
    state = empty_launch_graph_state()
    assert state["privacy_checked"] is False
    assert state["server_checked"] is False
    assert state["enter_tapped_count"] == 0
    assert state["iteration"] == 0


def test_mark_node_done_and_failed() -> None:
    state = empty_launch_graph_state()
    mark_node_done(state, "atomic_login", artifact="/tmp/a.png")
    assert state["completed_nodes"]["atomic_login"]["done"] is True
    assert node_attempts(state, "atomic_login") == 1

    mark_node_failed(state, "atomic_login", "bad creds")
    assert "atomic_login" in state["failed_nodes"]
    assert node_attempts(state, "atomic_login") == 2


def test_launch_facts_serialization() -> None:
    facts = LaunchFacts(
        login_blocking=True,
        login_stage="login_form",
        enter_cta_visible=True,
        enter_cta_xy=(100, 200),
        terms_checkbox_visible=True,
    )
    data = facts.model_dump()
    restored = LaunchFacts.model_validate(data)
    assert restored.login_blocking is True
    assert restored.enter_cta_xy == (100, 200)


def test_launch_node_status_model() -> None:
    status = LaunchNodeStatus(node="tap_enter_game", done=True, attempts=2)
    assert status.node == "tap_enter_game"
    assert status.attempts == 2
