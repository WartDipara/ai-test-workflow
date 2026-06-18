"""Core deliverable path resolution."""

from __future__ import annotations

from pathlib import Path

from game_agent.models.settings import AppConfig


def resolve_deliverables_dir(cfg: AppConfig) -> Path:
    return Path(cfg.gameturbo.run_outputs_dir)
