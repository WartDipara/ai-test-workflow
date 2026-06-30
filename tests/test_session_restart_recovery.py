"""进程重启后分场景恢复：状态重置与路由测试。"""

from __future__ import annotations

from game_agent.graphs.launch_routing import (
    plan_route,
    should_route_session_agent,
    should_route_session_relogin,
    should_route_scene,
)
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.modules.run_context import AttemptContext
from game_agent.services.session_restart_recovery import (
    apply_session_restart_to_state,
    classify_session_restart_phase,
    maybe_complete_session_relogin_recovery,
)


def _state(**kwargs):
    state = empty_launch_graph_state()
    state.update(kwargs)
    if isinstance(state.get("facts"), LaunchFacts):
        state["facts"] = state["facts"].model_dump()
    return state


def test_classify_post_login_in_game() -> None:
    state = _state(
        login_done=True,
        session_agent_active=True,
        in_game_agent_rounds=5,
    )
    assert classify_session_restart_phase(state) == "post_login_in_game"


def test_classify_during_login() -> None:
    state = _state(account_filled=True, password_filled=True, login_submitted=False)
    assert classify_session_restart_phase(state) == "during_login"


def test_classify_pre_login() -> None:
    state = _state(privacy_checked=True)
    assert classify_session_restart_phase(state) == "pre_login"


def test_post_login_restart_resets_login_and_agent() -> None:
    state = _state(
        login_done=True,
        account_filled=True,
        password_filled=True,
        login_submitted=True,
        session_agent_active=True,
        in_game_agent_rounds=12,
        enter_tapped_count=2,
    )
    phase = apply_session_restart_to_state(state, evidence="test_restart", session_index=3)

    assert phase == "post_login_in_game"
    assert state["login_done"] is False
    assert state["account_filled"] is False
    assert state["session_agent_active"] is False
    assert state["session_relogin_recovery_active"] is True
    assert state["session_restart_phase"] == "post_login_in_game"
    assert state["in_game_agent_rounds"] == 12


def test_during_login_restart_preserves_progress() -> None:
    state = _state(
        login_done=False,
        account_filled=True,
        password_filled=True,
        login_submitted=False,
        current_stage="login_form",
        tree_trace="privacy->atomic_login",
    )
    phase = apply_session_restart_to_state(state, evidence="mid_login", session_index=2)

    assert phase == "during_login"
    assert state["account_filled"] is True
    assert state["password_filled"] is True
    assert state["login_submitted"] is False
    assert state["session_relogin_recovery_active"] is True
    assert state["session_restart_phase"] == "during_login"
    assert state.get("session_restart_checkpoint", {}).get("tree_trace") == "privacy->atomic_login"


def test_pre_login_restart_keeps_normal_dfs() -> None:
    state = _state(privacy_checked=True, current_stage="launch")
    phase = apply_session_restart_to_state(state, evidence="early_crash", session_index=1)

    assert phase == "pre_login"
    assert state["session_relogin_recovery_active"] is False
    assert state["session_restart_phase"] == "pre_login"

    facts = LaunchFacts()
    state["facts"] = facts.model_dump()
    assert should_route_session_relogin(state, facts) is False


def test_recovery_routes_session_relogin_not_session_agent() -> None:
    facts = LaunchFacts()
    state = _state(
        login_done=False,
        session_agent_active=False,
        session_relogin_recovery_active=True,
        session_restart_phase="during_login",
        in_game_agent_rounds=0,
        facts=facts.model_dump(),
    )
    assert should_route_session_relogin(state, facts) is True
    assert should_route_session_agent(state, facts) is False
    assert should_route_scene(state, facts) is False
    assert plan_route(state) == "session_relogin"


def test_recovery_complete_reactivates_session_agent() -> None:
    facts = LaunchFacts()
    state = _state(
        login_done=True,
        session_relogin_recovery_active=True,
        session_restart_phase="post_login_in_game",
        in_game_agent_rounds=5,
        enter_tapped_count=1,
        facts=facts.model_dump(),
    )
    assert maybe_complete_session_relogin_recovery(state, facts) is True
    assert state["session_relogin_recovery_active"] is False
    assert state["session_agent_active"] is True


def test_attempt_context_consume_session_relogin_recovery() -> None:
    ctx = AttemptContext()
    ctx.request_session_relogin_recovery()
    assert ctx.consume_session_relogin_recovery() is True
    assert ctx.consume_session_relogin_recovery() is False
