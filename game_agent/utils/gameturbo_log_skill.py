"""Deprecated shim — use game_agent.external_services.gameturbo.retry.prompts."""

from game_agent.external_services.gameturbo.retry.prompts import (
    gameturbo_log_baseline_prompt_block,
    load_gameturbo_log_baseline_skill,
)

__all__ = ["gameturbo_log_baseline_prompt_block", "load_gameturbo_log_baseline_skill"]
