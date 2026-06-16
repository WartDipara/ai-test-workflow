"""LaunchStateStore：tree node id 与 getter/setter。"""

from __future__ import annotations

from game_agent.graphs.launch_state_store import (
    completed_tree_node,
    mark_tree_node_done,
    mark_tree_node_failed,
    node_attempts,
    set_login_done,
    set_sub_account_selected,
    tree_node_id_for_action,
)
from game_agent.models.launch_graph_state import empty_launch_graph_state


def test_tree_node_id_for_action() -> None:
    assert tree_node_id_for_action("select_sub_account") == "login.select_sub_account"
    assert tree_node_id_for_action("atomic_login") == "atomic_login"


def test_mark_tree_node_uses_tree_id_for_attempts() -> None:
    state = empty_launch_graph_state()
    for _ in range(3):
        mark_tree_node_failed(state, "select_sub_account", "no tap target")
    assert node_attempts(state, "login.select_sub_account") == 3
    assert node_attempts(state, "select_sub_account") == 3


def test_completed_tree_node_legacy_alias() -> None:
    state = empty_launch_graph_state()
    mark_tree_node_done(state, "handle_initial_privacy_dialog")
    assert completed_tree_node(state, "handle_initial_privacy_dialog")


def test_setters_milestones() -> None:
    state = empty_launch_graph_state()
    set_login_done(state, evidence="ok")
    assert state.get("login_done") is True
    set_sub_account_selected(state, evidence="delta")
    assert state.get("sub_account_selected") is True
