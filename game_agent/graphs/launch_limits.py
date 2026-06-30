"""从 settings / graph state 读取 LangGraph 轮次与熔断参数。"""

from __future__ import annotations

from game_agent.models.launch_graph_state import LaunchGraphState
from game_agent.models.settings import AppConfig, LaunchGraphSection


def launch_graph_limits_from_config(cfg: AppConfig) -> LaunchGraphSection:
    return cfg.launch_graph


def launch_graph_limits_from_state(state: LaunchGraphState) -> LaunchGraphSection:
    raw = state.get("launch_graph_limits")
    if isinstance(raw, dict) and raw:
        return LaunchGraphSection.model_validate(raw)
    return LaunchGraphSection()


def seed_launch_graph_limits(state: LaunchGraphState, cfg: AppConfig) -> None:
    state["launch_graph_limits"] = launch_graph_limits_from_config(cfg).model_dump()


def seed_launch_graph_executor_flags(state: LaunchGraphState, cfg: AppConfig) -> None:
    """将 executor 段开关写入 graph state，并在禁用时预置 server_checked。"""
    enabled = cfg.executor.server_selector_check_enabled
    state["server_selector_check_enabled"] = enabled
    if enabled:
        return
    from game_agent.graphs.launch_state_store import mark_tree_node_done, set_server_checked

    set_server_checked(state, evidence="skipped:config")
    mark_tree_node_done(state, "check_server_selector", evidence="skipped:config")
