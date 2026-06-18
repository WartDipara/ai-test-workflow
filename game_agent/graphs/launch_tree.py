"""进入游戏状态树：代码定义 guard/done/action，供 route_next DFS 遍历。"""

from __future__ import annotations

from game_agent.graphs.state_tree import StateTreeDecision, StateTreeNode, TreeTrace, dfs_next_action
from game_agent.graphs.launch_state_store import (
    completed_tree_node,
    is_login_done,
    is_privacy_checked,
    is_server_checked,
    is_sub_account_selected,
    node_attempts,
)
from game_agent.graphs.launch_limits import launch_graph_limits_from_state
from game_agent.models.launch_graph_state import (
    LaunchFacts,
    LaunchGraphState,
    LaunchRouteTarget,
)


def _attempts(state: LaunchGraphState, node_id: str) -> int:
    return node_attempts(state, node_id)


def _initial_privacy_active(s: LaunchGraphState, f: LaunchFacts) -> bool:
    """弹窗事实仍在，但节点已成功处理则不再阻塞后续流程。"""
    if not f.initial_privacy_dialog:
        return False
    return not completed_tree_node(s, "handle_initial_privacy_dialog")


def _login_blocking(s: LaunchGraphState, f: LaunchFacts) -> bool:
    """OCR 识别不到登录页时（如安全键盘黑屏），仍凭状态位留在登录子树。"""
    if is_login_done(s):
        return False
    if f.login_blocking:
        return True
    if s.get("login_submitted"):
        return False
    if s.get("account_filled") or s.get("password_filled"):
        return True
    return False


def _sub_account_blocking(_s: LaunchGraphState, f: LaunchFacts) -> bool:
    return f.sub_account_blocking and not is_sub_account_selected(_s)


def _overlay_blocking(_s: LaunchGraphState, f: LaunchFacts) -> bool:
    if not f.announcement_overlay:
        return False
    return not completed_tree_node(_s, "dismiss_blocking_overlay")


def _can_tap_enter(s: LaunchGraphState, f: LaunchFacts) -> bool:
    if _overlay_blocking(s, f):
        return False
    if _initial_privacy_active(s, f):
        return False
    if f.character_creation_blocking:
        return False
    if not f.enter_cta_visible or f.enter_cta_xy is None:
        return False
    if f.login_blocking or f.sub_account_blocking:
        return False
    if f.terms_checkbox_visible and not is_privacy_checked(s):
        return False
    if f.server_slot_visible and not is_server_checked(s) and is_login_done(s):
        return False
    return True


def _check_in_game_failed(s: LaunchGraphState) -> bool:
    failed = s.get("failed_nodes") or {}
    return "enter.check_in_game" in failed or "check_in_game" in failed


def _adaptive_phase_failed(s: LaunchGraphState) -> bool:
    failed = s.get("failed_nodes") or {}
    bucket = failed.get("adaptive_phase")
    return bool(isinstance(bucket, dict) and bucket.get("failed"))


def _needs_adaptive_phase(s: LaunchGraphState, f: LaunchFacts) -> bool:
    if not is_login_done(s):
        return False
    if s.get("in_game_confirmed"):
        return False
    if s.get("in_game_entry_passed"):
        return False
    if s.get("adaptive_flow_done"):
        return False
    limits = launch_graph_limits_from_state(s)
    if int(s.get("adaptive_rounds") or 0) >= limits.max_adaptive_rounds:
        return False
    if _adaptive_phase_failed(s):
        return False
    if f.login_blocking or f.sub_account_blocking:
        return False
    if f.initial_privacy_dialog:
        return False
    if s.get("adaptive_active_node_id"):
        return True
    if s.get("current_phase_spec"):
        return True
    if _check_in_game_failed(s):
        return True
    if f.character_creation_blocking:
        return True
    if f.interpreter_stage in ("character_creation", "unknown"):
        return True
    return False


def _should_check_in_game(s: LaunchGraphState, f: LaunchFacts) -> bool:
    if s.get("in_game_entry_passed"):
        return False
    if not s.get("adaptive_flow_done") and _needs_adaptive_phase(s, f):
        return False
    if _initial_privacy_active(s, f):
        return False
    if f.character_creation_blocking:
        return False
    if int(s.get("enter_tapped_count") or 0) < 1:
        return False
    if f.enter_cta_visible and f.enter_cta_xy is not None:
        return False
    if f.login_blocking or f.sub_account_blocking:
        return False
    if f.terms_checkbox_visible and not is_privacy_checked(s):
        return False
    if f.server_slot_visible and not is_server_checked(s):
        return False
    if not is_login_done(s) and f.login_stage == "login_form":
        return False
    return True


def _should_stability_observe(s: LaunchGraphState, _f: LaunchFacts) -> bool:
    return bool(s.get("in_game_entry_passed")) and not bool(s.get("in_game_confirmed"))


LAUNCH_TREE: StateTreeNode[LaunchGraphState, LaunchFacts, LaunchRouteTarget] = StateTreeNode(
    id="launch.root",
    action=None,
    guard=lambda _s, _f: True,
    done=lambda _s: bool(_s.get("finished") or _s.get("in_game_confirmed")),
    children=(
        StateTreeNode(
            id="privacy.initial_dialog",
            action="handle_initial_privacy_dialog",
            guard=_initial_privacy_active,
            done=lambda s: completed_tree_node(s, "handle_initial_privacy_dialog"),
            max_attempts=3,
        ),
        StateTreeNode(
            id="atomic_login",
            action="atomic_login",
            guard=_login_blocking,
            done=lambda s: is_login_done(s),
            max_attempts=3,
        ),
        StateTreeNode(
            id="login.select_sub_account",
            action="select_sub_account",
            guard=_sub_account_blocking,
            done=lambda s: is_sub_account_selected(s),
            max_attempts=3,
        ),
        StateTreeNode(
            id="download.handle",
            action="handle_download",
            guard=lambda _s, f: bool(f.download_visible),
            done=lambda s: completed_tree_node(s, "handle_download"),
            max_attempts=3,
        ),
        StateTreeNode(
            id="privacy.checkbox",
            action="ensure_privacy_checkbox",
            guard=lambda s, f: bool(f.terms_checkbox_visible) and not is_privacy_checked(s),
            done=lambda s: is_privacy_checked(s),
            max_attempts=3,
        ),
        StateTreeNode(
            id="overlay.dismiss",
            action="dismiss_blocking_overlay",
            guard=_overlay_blocking,
            done=lambda s: completed_tree_node(s, "dismiss_blocking_overlay"),
            max_attempts=3,
        ),
        StateTreeNode(
            id="server.check",
            action="check_server_selector",
            guard=lambda s, f: bool(
                f.server_slot_visible
                and not is_server_checked(s)
                and (is_login_done(s) or not f.login_blocking)
                and not _overlay_blocking(s, f)
            ),
            done=lambda s: is_server_checked(s),
            max_attempts=3,
        ),
        StateTreeNode(
            id="post_login.adaptive",
            action="adaptive_phase",
            guard=_needs_adaptive_phase,
            done=lambda s: bool(s.get("adaptive_flow_done")),
            max_attempts=16,
        ),
        StateTreeNode(
            id="enter.check_in_game",
            action="check_in_game",
            guard=_should_check_in_game,
            done=lambda s: bool(s.get("in_game_entry_passed")),
            max_attempts=3,
        ),
        StateTreeNode(
            id="enter.stability_observe",
            action="stability_observe",
            guard=_should_stability_observe,
            done=lambda s: bool(s.get("in_game_confirmed")),
            max_attempts=16,
        ),
        StateTreeNode(
            id="enter.tap",
            action="tap_enter_game",
            guard=_can_tap_enter,
            done=lambda _s: False,
            max_attempts=3,
        ),
    ),
)


def launch_dfs_next(
    state: LaunchGraphState,
    facts: LaunchFacts,
    *,
    trace: TreeTrace | None = None,
) -> StateTreeDecision[LaunchRouteTarget]:
    return dfs_next_action(
        LAUNCH_TREE,
        state,
        facts,
        node_attempts=_attempts,
        trace=trace,
    )
