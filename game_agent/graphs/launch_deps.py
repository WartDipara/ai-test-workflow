"""LangGraph 运行依赖（ADB、配置、RunState）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from game_agent.models.run_state import RunState
from game_agent.models.settings import AppConfig
from game_agent.modules.run_context import AttemptContext
from game_agent.services.adb_service import AdbService
from game_agent.graphs.vision_enrichment import VisionEnrichmentQueue
from game_agent.services.run_audit_log import RunAuditLogger


@dataclass
class LaunchGraphDeps:
    app_config: AppConfig
    adb: AdbService
    run_state: RunState
    artifact_root: Path
    settings_path: Path
    audit: RunAuditLogger | None = None
    attempt_context: AttemptContext | None = None
    round_id: int = 0
    screen_width: int = 0
    screen_height: int = 0
    vision_queue: VisionEnrichmentQueue | None = None
    external_log_reader: Callable[..., str] | None = None
    _graph_config: dict = field(default_factory=dict, repr=False)
