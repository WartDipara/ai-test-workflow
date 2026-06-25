"""GameTurbo plugin deliverable helpers."""

from __future__ import annotations

from game_agent.external_services.gameturbo.bootstrap import find_merged_config_for_deliverable
from game_agent.external_services.gameturbo.log.domain_extract import DEFAULT_OUTPUT_NAME
from game_agent.services.failure_report import (
    ATTEMPT_FAILURE_REPORT_JSON,
    ATTEMPT_FAILURE_REPORT_MD,
)

GAMETURBO_LOG_NAME = "gameturbo.log"
GAMETURBO_SESSION_LOG_GLOB = "gameturbo_session_*.log"

CORE_FAILURE_DELIVERABLE_FILES: tuple[str, ...] = (
    "ai_analysis_report.txt",
    ATTEMPT_FAILURE_REPORT_MD,
    ATTEMPT_FAILURE_REPORT_JSON,
    "process.log",
    "deploy.log",
    "pipeline_trace.jsonl",
)

GAMETURBO_FAILURE_DELIVERABLE_FILES: tuple[str, ...] = (
    GAMETURBO_LOG_NAME,
    DEFAULT_OUTPUT_NAME,
)


def failure_deliverable_files(*, gameturbo_enabled: bool) -> tuple[str, ...]:
    if gameturbo_enabled:
        return CORE_FAILURE_DELIVERABLE_FILES + GAMETURBO_FAILURE_DELIVERABLE_FILES
    return CORE_FAILURE_DELIVERABLE_FILES


__all__ = [
    "CORE_FAILURE_DELIVERABLE_FILES",
    "DEFAULT_OUTPUT_NAME",
    "GAMETURBO_FAILURE_DELIVERABLE_FILES",
    "GAMETURBO_LOG_NAME",
    "GAMETURBO_SESSION_LOG_GLOB",
    "failure_deliverable_files",
    "find_merged_config_for_deliverable",
]
