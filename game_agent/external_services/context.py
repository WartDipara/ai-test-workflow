from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from game_agent.models.task_config import TaskConfig
    from game_agent.modules.preprocessing.preprocessor import PreprocessResult
    from game_agent.services.adb_service import AdbService
    from game_agent.services.run_audit_log import RunAuditLogger


@dataclass(slots=True)
class ServiceContext:
    """Per-attempt context passed to external service plugins."""

    config_path: Path
    app_config: TaskConfig
    adb: AdbService
    artifact_root: Path
    deliverable_root: Path | None
    retry: int
    max_retries: int
    audit: RunAuditLogger | None = None
    preprocess_record: PreprocessResult | None = None
    last_ui_stage: str = ""
    last_ui_progress: str = ""
    plugin_state: dict[str, Any] = field(default_factory=dict)
