"""静态 LangGraph 大节点优先于 scene 策略的门禁。"""

from __future__ import annotations

from game_agent.graphs.launch_state_store import (
    completed_tree_node,
    is_login_done,
    is_privacy_checked,
    is_server_checked,
    is_sub_account_selected,
)
from game_agent.models.launch_graph_state import LaunchFacts, LaunchGraphState
from game_agent.services.privacy_gate import privacy_modal_still_open


def _privacy_milestone_pending(state: LaunchGraphState, facts: LaunchFacts) -> bool:
    if completed_tree_node(state, "handle_initial_privacy_dialog"):
        return False
    ocr = str(state.get("last_ocr_summary") or "")
    if facts.initial_privacy_dialog or facts.agree_button_xy is not None:
        return True
    return privacy_modal_still_open(ocr)


def has_pending_static_work(state: LaunchGraphState, facts: LaunchFacts) -> bool:
    """存在未完成的静态业务里程碑时，scene 不得抢路由。"""
    if _privacy_milestone_pending(state, facts):
        return True
    if facts.login_blocking:
        return True
    if facts.sub_account_blocking:
        return True
    if is_login_done(state) and not is_sub_account_selected(state) and facts.login_stage == "sub_account_select":
        return True
    if facts.terms_checkbox_visible and not is_privacy_checked(state):
        return True
    if facts.download_visible and not completed_tree_node(state, "handle_download"):
        return True
    if facts.announcement_overlay and not completed_tree_node(state, "dismiss_blocking_overlay"):
        return True
    if (
        facts.server_slot_visible
        and not is_server_checked(state)
        and state.get("server_selector_check_enabled", True)
    ):
        return True
    return False


def blocks_scene_routing(state: LaunchGraphState, facts: LaunchFacts) -> bool:
    """scene_action 是否应让路给静态节点。"""
    return has_pending_static_work(state, facts)
