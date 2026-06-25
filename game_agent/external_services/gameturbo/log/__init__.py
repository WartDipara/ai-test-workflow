from __future__ import annotations

from pathlib import Path

from game_agent.external_services.gameturbo.log_health import assess_gameturbo_log_health
from game_agent.services.adb_service import AdbService
from game_agent.services.external_log_base import (
    ExternalLogCollector,
    LogHealthVerdict,
    default_log_dedup_key,
    resolve_pipeline_artifact_root,
)

GAMETURBO_LOG_FILENAME = "gameturbo.log"


class GameTurboLogCollector(ExternalLogCollector):
    service_name = "GameTurbo"
    logcat_tag = "GameTurbo"
    log_filename = GAMETURBO_LOG_FILENAME
    session_prefix = "gameturbo_session"

    def format_latest_log_for_agent(
        self,
        artifact_root: Path,
        adb: AdbService | None = None,
        *,
        limit: int = 100,
        refresh_from_device: bool = True,
        include_health_hint: bool = True,
    ) -> str:
        lines, path = self.tail_log_lines(
            artifact_root,
            adb,
            limit=limit,
            refresh_from_device=refresh_from_device,
        )
        if not lines:
            return (
                f"No GameTurbo log lines yet ({path.name} missing or empty). "
                "Parallel log monitor may still be starting."
            )
        header = (
            f"Latest {len(lines)} GameTurbo log lines from {path.name} "
            "(newest at bottom; top-left overlay speed is NOT download progress):\n"
        )
        body = "\n".join(lines)
        if not include_health_hint:
            return header + body
        health = self.assess_health("\n".join(lines))
        footer = (
            f"\n\n[automated log health hint] suspect=true reason={health.reason}"
            if health.suspect
            else "\n\n[automated log health hint] suspect=false"
        )
        return header + body + footer

    def assess_health(self, log_text: str, *, ui_stage: str = "") -> LogHealthVerdict:
        return assess_gameturbo_log_health(log_text, ui_stage=ui_stage)


GAMETURBO_LOG_COLLECTOR = GameTurboLogCollector()


def gameturbo_log_path(artifact_root: Path | None) -> Path:
    return GAMETURBO_LOG_COLLECTOR.log_path(artifact_root)


def gameturbo_log_dedup_key(line: str) -> str:
    return default_log_dedup_key(line)


def fetch_device_gameturbo_lines(adb: AdbService, *, timeout_s: float = 60.0) -> list[str]:
    return GAMETURBO_LOG_COLLECTOR.fetch_device_lines(adb, timeout_s=timeout_s)


def read_gameturbo_dedup_keys(path: Path) -> set[str]:
    return GAMETURBO_LOG_COLLECTOR.read_dedup_keys(path)


def clear_device_logcat(adb: AdbService, *, timeout_s: float = 15.0) -> None:
    GAMETURBO_LOG_COLLECTOR.clear_device_logcat(adb, timeout_s=timeout_s)


def merge_gameturbo_session_archives(artifact_root: Path) -> Path:
    return GAMETURBO_LOG_COLLECTOR.merge_session_archives(artifact_root)


def ensure_gameturbo_log_for_analysis(artifact_root: Path) -> Path | None:
    return GAMETURBO_LOG_COLLECTOR.ensure_log_for_analysis(artifact_root)


def rotate_gameturbo_log(artifact_root: Path, *, session_index: int) -> Path | None:
    return GAMETURBO_LOG_COLLECTOR.rotate_log(artifact_root, session_index=session_index)


def bootstrap_gameturbo_log(adb: AdbService, artifact_root: Path) -> Path:
    return GAMETURBO_LOG_COLLECTOR.bootstrap_log(adb, artifact_root)


def append_gameturbo_line(path: Path, line: str) -> None:
    GAMETURBO_LOG_COLLECTOR.append_line(path, line)


def append_gameturbo_stage_marker(artifact_root: Path, phase: str, note: str = "") -> None:
    GAMETURBO_LOG_COLLECTOR.append_stage_marker(artifact_root, phase, note)


def tail_gameturbo_log_lines(
    artifact_root: Path,
    adb: AdbService | None = None,
    *,
    limit: int = 100,
    refresh_from_device: bool = True,
) -> tuple[list[str], Path]:
    return GAMETURBO_LOG_COLLECTOR.tail_log_lines(
        artifact_root,
        adb,
        limit=limit,
        refresh_from_device=refresh_from_device,
    )


def format_latest_gameturbo_log_for_agent(
    artifact_root: Path,
    adb: AdbService | None = None,
    *,
    limit: int = 100,
    refresh_from_device: bool = True,
    include_health_hint: bool = True,
) -> str:
    return GAMETURBO_LOG_COLLECTOR.format_latest_log_for_agent(
        artifact_root,
        adb,
        limit=limit,
        refresh_from_device=refresh_from_device,
        include_health_hint=include_health_hint,
    )


def finalize_gameturbo_log(adb: AdbService, artifact_root: Path) -> Path | None:
    return GAMETURBO_LOG_COLLECTOR.finalize_log(adb, artifact_root)


__all__ = [
    "GAMETURBO_LOG_COLLECTOR",
    "GAMETURBO_LOG_FILENAME",
    "GameTurboLogCollector",
    "append_gameturbo_line",
    "append_gameturbo_stage_marker",
    "bootstrap_gameturbo_log",
    "clear_device_logcat",
    "ensure_gameturbo_log_for_analysis",
    "fetch_device_gameturbo_lines",
    "finalize_gameturbo_log",
    "format_latest_gameturbo_log_for_agent",
    "gameturbo_log_dedup_key",
    "gameturbo_log_path",
    "merge_gameturbo_session_archives",
    "read_gameturbo_dedup_keys",
    "resolve_pipeline_artifact_root",
    "rotate_gameturbo_log",
    "tail_gameturbo_log_lines",
]
