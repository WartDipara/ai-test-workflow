"""Optional external plugin log reader for LangGraph / executor."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game_agent.models.settings import AppConfig
    from game_agent.services.adb_service import AdbService


ExternalLogReader = Callable[..., str]


def resolve_external_log_reader(cfg: AppConfig) -> ExternalLogReader | None:
    from game_agent.external_services.manager import ExternalServiceManager

    return ExternalServiceManager(cfg).resolve_log_reader()


async def fetch_external_log_summary(
    reader: ExternalLogReader | None,
    *,
    artifact_root,
    adb: AdbService,
    limit: int = 80,
    refresh_from_device: bool = True,
    include_health_hint: bool = False,
) -> str:
    if reader is None:
        return ""
    import asyncio

    return await asyncio.to_thread(
        reader,
        artifact_root,
        adb,
        limit=limit,
        refresh_from_device=refresh_from_device,
        include_health_hint=include_health_hint,
    )
