"""LaunchGraph state 读写：统一 tree node id、里程碑 getter/setter、节点标记。"""

from __future__ import annotations

from typing import Any

from game_agent.models.launch_graph_state import (
    LaunchGraphState,
    mark_node_done,
    mark_node_failed,
    node_attempts as _raw_node_attempts,
)

# LangGraph action → DFS 状态树 node.id
ACTION_TO_TREE_NODE: dict[str, str] = {
    "handle_initial_privacy_dialog": "privacy.initial_dialog",
    "atomic_login": "atomic_login",
    "select_sub_account": "login.select_sub_account",
    "handle_download": "download.handle",
    "ensure_privacy_checkbox": "privacy.checkbox",
    "dismiss_blocking_overlay": "overlay.dismiss",
    "check_server_selector": "server.check",
    "check_in_game": "enter.check_in_game",
    "stability_observe": "enter.stability_observe",
    "adaptive_phase": "post_login.adaptive",
    "tap_enter_game": "enter.tap",
    "dynamic_action": "dynamic.explore",
    "scene_action": "scene.explore",
    "free": "free.explore",
    "recover_from_failure": "recover_from_failure",
}

TREE_NODE_TO_ACTION: dict[str, str] = {v: k for k, v in ACTION_TO_TREE_NODE.items()}

# completed_nodes 可能存 action 名，查找 attempts 时做映射
_LEGACY_NODE_ALIASES: dict[str, tuple[str, ...]] = {
    "privacy.initial_dialog": ("handle_initial_privacy_dialog",),
    "login.select_sub_account": ("select_sub_account",),
    "download.handle": ("handle_download",),
    "privacy.checkbox": ("ensure_privacy_checkbox",),
    "overlay.dismiss": ("dismiss_blocking_overlay",),
    "server.check": ("check_server_selector",),
    "enter.check_in_game": ("check_in_game",),
    "enter.tap": ("tap_enter_game",),
}


def tree_node_id_for_action(action: str) -> str:
    return ACTION_TO_TREE_NODE.get(action, action)


def action_for_tree_node(tree_node_id: str) -> str:
    return TREE_NODE_TO_ACTION.get(tree_node_id, tree_node_id)


def normalize_node_key(node: str) -> str:
    """将 action 名或 tree id 规范为 tree node id。"""
    if node in TREE_NODE_TO_ACTION:
        return node
    return ACTION_TO_TREE_NODE.get(node, node)


def node_attempts(state: LaunchGraphState, node: str) -> int:
    """按 tree node id 计次；兼容旧 action 名 bucket。"""
    key = normalize_node_key(node)
    attempts = _raw_node_attempts(state, key)
    if attempts > 0:
        return attempts
    for alias in _LEGACY_NODE_ALIASES.get(key, ()):
        attempts = max(attempts, _raw_node_attempts(state, alias))
    if attempts > 0:
        return attempts
    if key != node:
        return _raw_node_attempts(state, node)
    return 0


def mark_tree_node_done(
    state: LaunchGraphState,
    action: str,
    *,
    artifact: str = "",
    evidence: str = "",
) -> None:
    key = tree_node_id_for_action(action)
    mark_node_done(state, key, artifact=artifact, evidence=evidence)


def mark_tree_node_failed(
    state: LaunchGraphState,
    action: str,
    error: str,
    *,
    artifact: str = "",
    evidence: str = "",
) -> None:
    key = tree_node_id_for_action(action)
    mark_node_failed(state, key, error, artifact=artifact, evidence=evidence)


def get_last_ocr(state: LaunchGraphState) -> str:
    return str(state.get("last_ocr_summary") or "")


def set_last_ocr(state: LaunchGraphState, ocr_summary: str) -> None:
    state["last_ocr_summary"] = ocr_summary


def is_login_done(state: LaunchGraphState) -> bool:
    return bool(state.get("login_done"))


def set_login_done(
    state: LaunchGraphState,
    *,
    evidence: str = "",
    account_filled: bool = True,
    password_filled: bool = True,
    login_submitted: bool = True,
) -> None:
    state["account_filled"] = account_filled
    state["password_filled"] = password_filled
    state["login_submitted"] = login_submitted
    state["login_done"] = True
    if evidence:
        state["recover_hint"] = evidence[:500]


def is_sub_account_selected(state: LaunchGraphState) -> bool:
    return bool(state.get("sub_account_selected"))


def set_sub_account_selected(state: LaunchGraphState, *, evidence: str = "") -> None:
    state["sub_account_selected"] = True
    if evidence:
        hints = str(state.get("recover_hint") or "")
        state["recover_hint"] = f"{hints}; sub_account={evidence[:200]}".strip("; ")


def is_privacy_checked(state: LaunchGraphState) -> bool:
    return bool(state.get("privacy_checked"))


def set_privacy_checked(state: LaunchGraphState, *, evidence: str = "") -> None:
    state["privacy_checked"] = True
    if evidence:
        _noop = evidence  # reserved for audit extension


def is_server_checked(state: LaunchGraphState) -> bool:
    return bool(state.get("server_checked"))


def set_server_checked(state: LaunchGraphState, *, evidence: str = "") -> None:
    state["server_checked"] = True
    if evidence:
        _noop = evidence


def increment_enter_tapped(state: LaunchGraphState) -> int:
    count = int(state.get("enter_tapped_count") or 0) + 1
    state["enter_tapped_count"] = count
    return count


def set_in_game_confirmed(state: LaunchGraphState, *, evidence: str = "") -> None:
    state["in_game_confirmed"] = True
    state["finished"] = True
    if evidence:
        state["recover_hint"] = evidence[:500]


def get_node_evidence(state: LaunchGraphState, action: str) -> str:
    key = tree_node_id_for_action(action)
    for bucket in (state.get("completed_nodes") or {}, state.get("failed_nodes") or {}):
        if key in bucket:
            return str(bucket[key].get("evidence", "") or "")
    return ""


def completed_tree_node(state: LaunchGraphState, action: str) -> bool:
    key = tree_node_id_for_action(action)
    completed = state.get("completed_nodes") or {}
    if key in completed and completed[key].get("done"):
        return True
    for alias in _LEGACY_NODE_ALIASES.get(key, ()):
        if alias in completed and completed[alias].get("done"):
            return True
    return False


def clear_completed_node(state: LaunchGraphState, action: str) -> None:
    key = tree_node_id_for_action(action)
    completed = dict(state.get("completed_nodes") or {})
    completed.pop(key, None)
    for alias in _LEGACY_NODE_ALIASES.get(key, ()):
        completed.pop(alias, None)
    state["completed_nodes"] = completed


def reset_login_progress(state: LaunchGraphState, *, evidence: str = "") -> None:
    state["account_filled"] = False
    state["password_filled"] = False
    state["login_submitted"] = False
    state["login_done"] = False
    clear_completed_node(state, "atomic_login")
    if evidence:
        state["recover_hint"] = evidence[:500]


def clear_failed_node(state: LaunchGraphState, action: str) -> None:
    key = tree_node_id_for_action(action)
    failed = dict(state.get("failed_nodes") or {})
    failed.pop(key, None)
    for alias in _LEGACY_NODE_ALIASES.get(key, ()):
        failed.pop(alias, None)
    state["failed_nodes"] = failed


def migrate_legacy_node_keys(state: LaunchGraphState) -> None:
    """将 completed/failed 中旧 action 名迁移为 tree node id。"""
    for bucket_name in ("completed_nodes", "failed_nodes"):
        bucket = dict(state.get(bucket_name) or {})
        migrated: dict[str, Any] = {}
        for key, value in bucket.items():
            new_key = normalize_node_key(key)
            if new_key in migrated:
                prev_attempts = int(migrated[new_key].get("attempts", 0))
                cur_attempts = int(value.get("attempts", 0))
                if cur_attempts >= prev_attempts:
                    migrated[new_key] = value
            else:
                migrated[new_key] = value
        state[bucket_name] = migrated
