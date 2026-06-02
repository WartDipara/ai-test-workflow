from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from game_agent.models.run_state import RunState
from game_agent.models.settings import AppConfig
from game_agent.modules.run_context import AttemptContext
from game_agent.services.adb_service import AdbService
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.views.console_view import ConsoleView


@dataclass(slots=True)
class ExecutorAgentDeps:
    """Runtime dependencies injected into executor Agent tools."""

    app_config: AppConfig
    adb: AdbService
    run_state: RunState
    artifact_root: Path
    view: ConsoleView
    screen_width: int
    screen_height: int
    audit: RunAuditLogger | None = None
    round_id: int = 0
    settings_path: Path | None = None
    attempt_context: AttemptContext | None = None
