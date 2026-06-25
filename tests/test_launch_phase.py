"""分层阶段门禁：纯状态机断言。"""

from __future__ import annotations

from game_agent.graphs.launch_phase import (
    in_game_entry_allowed,
    is_login_active,
    is_post_login,
    is_pre_login_scene_allowed,
    ocr_credential_login_passed,
    reconcile_login_state,
    vlm_login_verify_passed,
)
from game_agent.graphs.launch_routing import plan_route, should_route_free, should_route_scene
from game_agent.graphs.launch_state_store import mark_tree_node_done
from game_agent.graphs.launch_tree import launch_dfs_next
from game_agent.graphs.state_tree import StateTreeDecision
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state


def test_login_active_when_login_form_despite_login_done_flag() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    facts = LaunchFacts(login_blocking=True, login_stage="login_form")
    assert is_login_active(state, facts) is True
    assert is_post_login(state, facts) is False


def test_reconcile_resets_false_login_done_on_login_form() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    mark_tree_node_done(state, "atomic_login")
    facts = LaunchFacts(login_blocking=True, login_stage="login_form")
    state["facts"] = facts.model_dump()
    reconcile_login_state(state, facts)
    assert state.get("login_done") is False
    assert plan_route(state) == "atomic_login"


def test_pre_login_dialogue_routes_scene() -> None:
    state = empty_launch_graph_state()
    state["scene_id"] = "dialogue"
    state["scene_confidence"] = 0.72
    facts = LaunchFacts(scene_id="dialogue", scene_confidence=0.72)
    state["facts"] = facts.model_dump()
    assert is_pre_login_scene_allowed(state, facts, scene_id="dialogue", confidence=0.72)
    assert should_route_scene(state, facts) is True
    assert plan_route(state) == "scene_action"


def test_pre_login_dialogue_blocked_on_login_form() -> None:
    state = empty_launch_graph_state()
    state["scene_id"] = "dialogue"
    state["scene_confidence"] = 0.72
    facts = LaunchFacts(
        login_blocking=True,
        login_stage="login_form",
        scene_id="dialogue",
        scene_confidence=0.72,
    )
    state["facts"] = facts.model_dump()
    assert should_route_scene(state, facts) is False
    assert launch_dfs_next(state, facts).action == "atomic_login"


def test_fake_login_done_on_login_form_cannot_free() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["privacy_checked"] = True
    facts = LaunchFacts(
        login_blocking=True,
        login_stage="login_form",
        character_creation_blocking=True,
    )
    state["facts"] = facts.model_dump()
    decision = StateTreeDecision(action=None, node_id="launch.root", reason="no_eligible")
    assert should_route_free(state, facts, decision) is False
    assert plan_route(state) == "atomic_login"


def test_in_game_entry_cleared_without_enter_tap() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["in_game_entry_passed"] = True
    facts = LaunchFacts()
    reconcile_login_state(state, facts)
    assert state.get("in_game_entry_passed") is False
    assert in_game_entry_allowed(state, facts) is False


def test_in_game_entry_allowed_after_enter_tap() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["enter_tapped_count"] = 1
    facts = LaunchFacts()
    assert in_game_entry_allowed(state, facts) is True


def test_ocr_credential_login_passed_after_sub_account() -> None:
    assert ocr_credential_login_passed(left_login_form=True, stage="sub_account_select") is True
    assert ocr_credential_login_passed(left_login_form=True, stage="login_form") is False
    assert ocr_credential_login_passed(left_login_form=False, stage="clear") is False


def test_vlm_login_verify_ignores_stale_login_screen_on_sub_account() -> None:
    state = empty_launch_graph_state()
    state["last_game_entry_judgment"] = {
        "stage": "resource_download",
        "confidence": 0.92,
        "blockers": ["login_screen", "sub_account_select"],
        "in_game": False,
    }
    facts = LaunchFacts(login_stage="sub_account_select", login_blocking=False)
    assert vlm_login_verify_passed(state, facts=facts) is True


def test_is_login_active_false_on_enter_gate_after_sub_account() -> None:
    state = empty_launch_graph_state()
    state["last_game_entry_judgment"] = {
        "stage": "resource_download",
        "confidence": 0.92,
        "blockers": ["login_screen"],
        "in_game": False,
    }
    mark_tree_node_done(state, "select_sub_account")
    facts = LaunchFacts(
        enter_cta_visible=True,
        login_stage="clear",
        login_blocking=False,
    )
    assert is_login_active(state, facts) is False
