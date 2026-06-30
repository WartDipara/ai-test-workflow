"""DFS 状态树：登录子树、隐私优先、进游戏门控。"""

from __future__ import annotations

from game_agent.graphs.launch_tree import LAUNCH_TREE, _login_blocking, launch_dfs_next
from game_agent.graphs.state_tree import TreeTrace, dfs_next_action
from game_agent.graphs.launch_state_store import mark_tree_node_failed, node_attempts
from game_agent.models.launch_graph_state import (
    LaunchFacts,
    empty_launch_graph_state,
    mark_node_done,
)


def _state(**kwargs):
    state = empty_launch_graph_state()
    facts = kwargs.pop("facts", LaunchFacts())
    state["facts"] = facts.model_dump()
    state.update(kwargs)
    return state


def _decide(**kwargs):
    state = _state(**kwargs)
    facts = LaunchFacts.model_validate(state["facts"])
    return launch_dfs_next(state, facts)


def test_dfs_login_routes_atomic_login() -> None:
    d = _decide(
        facts=LaunchFacts(login_blocking=True, login_stage="login_form"),
        login_done=False,
    )
    assert d.action == "atomic_login"
    assert d.node_id == "atomic_login"


def test_dfs_skips_atomic_login_when_done() -> None:
    d = _decide(
        facts=LaunchFacts(
            login_blocking=True,
            login_stage="sub_account_select",
            sub_account_blocking=True,
            sub_account_action_xy=(100, 200),
        ),
        login_done=True,
    )
    assert d.action == "select_sub_account"


def test_dfs_sub_account_after_login() -> None:
    d = _decide(
        facts=LaunchFacts(
            sub_account_blocking=True,
            login_stage="sub_account_select",
            sub_account_action_xy=(100, 200),
        ),
        login_done=True,
    )
    assert d.action == "select_sub_account"


def test_dfs_privacy_modal_before_login() -> None:
    d = _decide(
        facts=LaunchFacts(
            initial_privacy_dialog=True,
            agree_button_xy=(500, 900),
            login_blocking=True,
            login_stage="login_form",
        ),
    )
    assert d.action == "handle_initial_privacy_dialog"


def test_dfs_checkbox_gate_before_enter() -> None:
    d = _decide(
        facts=LaunchFacts(
            terms_checkbox_visible=True,
            enter_cta_visible=True,
            enter_cta_xy=(400, 800),
        ),
        privacy_checked=False,
        login_done=True,
        server_checked=True,
    )
    assert d.action == "ensure_privacy_checkbox"


def test_dfs_checkbox_gate_before_login() -> None:
    d = _decide(
        facts=LaunchFacts(
            terms_checkbox_visible=True,
            login_blocking=True,
            login_stage="login_form",
        ),
        privacy_checked=False,
        login_done=False,
    )
    assert d.action == "ensure_privacy_checkbox"
    assert d.node_id == "privacy.checkbox"


def test_dfs_announcement_overlay_before_server() -> None:
    d = _decide(
        facts=LaunchFacts(
            announcement_overlay=True,
            enter_cta_visible=True,
            enter_cta_xy=(538, 1980),
            server_slot_visible=True,
        ),
        login_done=True,
        privacy_checked=True,
        server_checked=False,
    )
    assert d.action == "dismiss_blocking_overlay"
    assert d.node_id == "overlay.dismiss"


def test_dfs_server_check_blocked_by_announcement() -> None:
    d = _decide(
        facts=LaunchFacts(
            announcement_overlay=True,
            server_slot_visible=True,
        ),
        login_done=True,
        privacy_checked=True,
    )
    assert d.action == "dismiss_blocking_overlay"


def test_dfs_skips_server_check_when_disabled() -> None:
    d = _decide(
        facts=LaunchFacts(
            server_slot_visible=True,
            enter_cta_visible=True,
            enter_cta_xy=(400, 800),
        ),
        login_done=True,
        privacy_checked=True,
        server_checked=True,
        server_selector_check_enabled=False,
    )
    assert d.action == "tap_enter_game"


def test_dfs_check_in_game_after_enter_tap_even_if_cta_visible() -> None:
    d = _decide(
        facts=LaunchFacts(
            enter_cta_visible=True,
            enter_cta_xy=(400, 800),
        ),
        privacy_checked=True,
        login_done=True,
        server_checked=True,
        enter_tapped_count=1,
    )
    assert d.action == "check_in_game"


def test_dfs_check_in_game_when_enter_tapped_and_no_cta() -> None:
    d = _decide(
        facts=LaunchFacts(enter_cta_visible=False),
        privacy_checked=True,
        login_done=True,
        server_checked=True,
        enter_tapped_count=1,
    )
    assert d.action == "check_in_game"


def test_dfs_stability_observe_after_entry_passed() -> None:
    d = _decide(
        facts=LaunchFacts(enter_cta_visible=False),
        privacy_checked=True,
        login_done=True,
        server_checked=True,
        enter_tapped_count=1,
        in_game_entry_passed=True,
        in_game_confirmed=False,
    )
    assert d.action == "stability_observe"
    assert d.node_id == "enter.stability_observe"


def test_dfs_in_game_agent_after_stability_complete() -> None:
    d = _decide(
        facts=LaunchFacts(enter_cta_visible=False),
        privacy_checked=True,
        login_done=True,
        server_checked=True,
        enter_tapped_count=1,
        in_game_entry_passed=True,
        stability_observe_complete=True,
        in_game_agent_started_at=1.0,
        in_game_confirmed=False,
    )
    assert d.action == "in_game_agent"
    assert d.node_id == "enter.in_game_agent"


def test_dfs_skips_done_privacy_dialog() -> None:
    state = _state(
        facts=LaunchFacts(
            initial_privacy_dialog=True,
            agree_button_xy=(500, 900),
            enter_cta_visible=True,
            enter_cta_xy=(400, 800),
        ),
        privacy_checked=False,
        login_done=True,
        server_checked=True,
    )
    mark_node_done(state, "handle_initial_privacy_dialog")
    facts = LaunchFacts.model_validate(state["facts"])
    d = launch_dfs_next(state, facts)
    assert d.action == "tap_enter_game"


def test_dfs_respects_max_attempts() -> None:
    state = _state(
        facts=LaunchFacts(login_blocking=True, login_stage="login_form"),
    )
    for _ in range(3):
        mark_node_done(state, "atomic_login")
    facts = LaunchFacts.model_validate(state["facts"])
    trace = TreeTrace()
    d = launch_dfs_next(state, facts, trace=trace)
    assert d.action != "atomic_login"
    assert node_attempts(state, "atomic_login") >= 3


def test_dfs_respects_max_attempts_sub_account() -> None:
    state = _state(
        facts=LaunchFacts(
            sub_account_blocking=True,
            login_stage="sub_account_select",
        ),
        login_done=True,
    )
    for _ in range(3):
        mark_tree_node_failed(state, "select_sub_account", "no tap")
    facts = LaunchFacts.model_validate(state["facts"])
    d = launch_dfs_next(state, facts)
    assert d.action != "select_sub_account"
    assert node_attempts(state, "login.select_sub_account") >= 3


def test_generic_dfs_trace() -> None:
    state = _state(
        facts=LaunchFacts(login_blocking=True, login_stage="login_form"),
    )
    facts = LaunchFacts.model_validate(state["facts"])
    trace = TreeTrace()
    dfs_next_action(
        LAUNCH_TREE,
        state,
        facts,
        node_attempts=lambda s, n: node_attempts(s, n),
        trace=trace,
    )
    assert "launch.root" in trace.visited
    assert trace.selected_node == "atomic_login"


def test_login_blocking_false_after_sub_account_completed() -> None:
    from game_agent.graphs.launch_state_store import mark_tree_node_done

    state = _state(
        facts=LaunchFacts(
            enter_cta_visible=True,
            login_stage="clear",
            login_blocking=False,
        ),
        login_done=False,
        account_filled=True,
    )
    mark_tree_node_done(state, "select_sub_account")
    facts = LaunchFacts.model_validate(state["facts"])
    assert _login_blocking(state, facts) is False
