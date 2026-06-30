"""登录后「进入游戏」起的 Session Agent 阶段边界。"""

from __future__ import annotations

import time

from game_agent.graphs.launch_state_store import completed_tree_node
from game_agent.models.launch_graph_state import LaunchGraphState
from game_agent.services.scene_strategies import clear_scene_strategy


def session_agent_eligible(state: LaunchGraphState) -> bool:
    """是否已离开选服/登录壳、应由 Agent 接管（含等价进入）。"""
    if state.get("session_relogin_recovery_active"):
        return False
    if state.get("session_agent_active"):
        return True
    if completed_tree_node(state, "tap_enter_game"):
        return True
    if int(state.get("enter_tapped_count") or 0) >= 1:
        return True
    return False


def activate_session_agent(state: LaunchGraphState, *, reason: str = "") -> bool:
    """
    点击「进入游戏」或等价入口后：启用 Agent 循环，关闭刚性 scene_strategy。
    返回 True 表示本次调用新激活。
    """
    if state.get("session_agent_active"):
        return False
    if not session_agent_eligible(state):
        return False
    now = time.monotonic()
    state["session_agent_active"] = True
    state["session_agent_started_at"] = now
    clear_scene_strategy(state)
    state["scene_strategy_active"] = False
    state["active_scene_strategy"] = ""
    return True


def ensure_session_agent_if_eligible(state: LaunchGraphState) -> bool:
    """classify 等路径上的安全网：满足条件则激活。"""
    if state.get("session_relogin_recovery_active"):
        return False
    if state.get("session_agent_active"):
        return False
    return activate_session_agent(state, reason="eligible")
