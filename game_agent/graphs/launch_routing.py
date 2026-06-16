"""LangGraph 路由：DFS 状态树选择下一节点。"""

from __future__ import annotations

import logging

from game_agent.graphs.launch_tree import TreeTrace, launch_dfs_next
from game_agent.graphs.launch_state_store import node_attempts
from game_agent.models.launch_graph_state import (
    MAX_NODE_ATTEMPTS,
    LaunchGraphState,
    LaunchRouteTarget,
    facts_from_state,
)

logger = logging.getLogger(__name__)


def plan_route(state: LaunchGraphState) -> LaunchRouteTarget:
    """
    计算下一节点并写入 state（须在节点返回值中调用，勿在 LangGraph 条件边回调里改 state）。
    """
    if state.get("finished") or state.get("terminal_error"):
        target: LaunchRouteTarget = "end"
    elif state.get("in_game_confirmed"):
        target = "end"
    else:
        logger.info(
            "[LaunchGraph:route] login_flags account_filled=%s password_filled=%s "
            "login_submitted=%s login_done=%s",
            state.get("account_filled"),
            state.get("password_filled"),
            state.get("login_submitted"),
            state.get("login_done"),
        )
        facts = facts_from_state(state)
        trace = TreeTrace()
        decision = launch_dfs_next(state, facts, trace=trace)
        state["current_tree_node"] = decision.node_id
        state["tree_trace"] = "->".join(trace.visited[-12:]) if trace.visited else decision.reason
        if decision.action is not None:
            target = decision.action
        elif node_attempts(state, "recover_from_failure") >= MAX_NODE_ATTEMPTS:
            target = "end"
        else:
            target = "recover_from_failure"

    state["last_route"] = target
    state["planned_next_route"] = target
    return target


def route_next(state: LaunchGraphState) -> LaunchRouteTarget:
    """计算并写入路由元数据（测试与单步调试；LangGraph 边用 consume_planned_route）。"""
    return plan_route(state)


def consume_planned_route(state: LaunchGraphState) -> LaunchRouteTarget:
    """LangGraph 条件边：读取 classify 写入的 planned_next_route。"""
    target = state.pop("planned_next_route", None)
    if target:
        return target  # type: ignore[return-value]
    return plan_route(state)
