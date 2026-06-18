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
