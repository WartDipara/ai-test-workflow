from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from game_agent.services.adb_service import AdbService

logger = logging.getLogger(__name__)

_LOGCAT_TS_PATTERNS = (
    re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})"),
    re.compile(r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})"),
)


def default_log_dedup_key(line: str) -> str:
    stripped = line.strip()
    for pattern in _LOGCAT_TS_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return match.group(1)
    return f"__no_ts__:{stripped}"


def resolve_pipeline_artifact_root(artifact_root: Path) -> Path:
    if artifact_root.name == "executor":
        return artifact_root.parent
    return artifact_root


def _count_nonempty_lines(path: Path) -> int:
    return sum(
        1
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    )


def _iter_nonempty_log_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]


@dataclass(frozen=True, slots=True)
class LogHealthVerdict:
    suspect: bool
    reason: str
    markers: tuple[str, ...] = ()


class ExternalLogCollector:
    """Base logcat collector used by external plugins.

    Core controllers depend on this abstraction; concrete plugins provide tag,
    filenames, and optional health analysis.
    """

    service_name: str = "external"
    logcat_tag: str = ""
    log_filename: str = "external.log"
    session_prefix: str = "external_session"

    def log_path(self, artifact_root: Path | None) -> Path:
        if artifact_root is not None:
            return artifact_root / self.log_filename
        return Path(self.log_filename)

    def session_archive_path(self, artifact_root: Path, session_index: int) -> Path:
        return artifact_root / f"{self.session_prefix}_{session_index:03d}.log"

    def log_dedup_key(self, line: str) -> str:
        return default_log_dedup_key(line)

    def fetch_device_lines(self, adb: AdbService, *, timeout_s: float = 60.0) -> list[str]:
        if not self.logcat_tag:
            return []
        try:
            result = subprocess.run(
                adb._base() + ["logcat", "-d", "-s", self.logcat_tag],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("[%s] logcat -d 超时 (%.0fs)", self.service_name, timeout_s)
            return []
        return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]

    def read_dedup_keys(self, path: Path) -> set[str]:
        if not path.is_file():
            return set()
        return {
            self.log_dedup_key(line)
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        }

    def append_unique_lines(self, path: Path, lines: list[str], seen_keys: set[str]) -> int:
        added = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            for line in lines:
                key = self.log_dedup_key(line)
                if key in seen_keys:
                    continue
                f.write(line + "\n")
                seen_keys.add(key)
                added += 1
        return added

    def clear_device_logcat(self, adb: AdbService, *, timeout_s: float = 15.0) -> None:
        try:
            subprocess.run(
                adb._base() + ["logcat", "-c"],
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
            logger.info("[%s] 已执行 logcat -c", self.service_name)
        except subprocess.TimeoutExpired:
            logger.warning("[%s] logcat -c 超时 (%.0fs)", self.service_name, timeout_s)
            raise

    def merge_session_archives(self, artifact_root: Path) -> Path:
        active = self.log_path(artifact_root)
        archives = sorted(artifact_root.glob(f"{self.session_prefix}_*.log"))
        if not archives:
            return active

        seen_keys: set[str] = set()
        merged: list[str] = []
        for archive in archives:
            for line in _iter_nonempty_log_lines(archive):
                key = self.log_dedup_key(line)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged.append(line)

        for line in _iter_nonempty_log_lines(active):
            key = self.log_dedup_key(line)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(line)

        active.parent.mkdir(parents=True, exist_ok=True)
        if merged:
            active.write_text("\n".join(merged) + "\n", encoding="utf-8")
            logger.info(
                "[%s] 已合并 %d 个会话归档到 %s | 共 %d 行",
                self.service_name,
                len(archives),
                self.log_filename,
                len(merged),
            )
        return active

    def ensure_log_for_analysis(self, artifact_root: Path) -> Path | None:
        path = self.log_path(artifact_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        archives = sorted(artifact_root.glob(f"{self.session_prefix}_*.log"))
        if archives:
            self.merge_session_archives(artifact_root)
            path = self.log_path(artifact_root)
        if path.is_file() and _count_nonempty_lines(path) > 0:
            return path
        latest = archives[-1] if archives else None
        if latest is not None and _count_nonempty_lines(latest) > 0:
            shutil.copy2(latest, path)
        elif not path.is_file():
            path.write_text("", encoding="utf-8")
        if path.is_file() and _count_nonempty_lines(path) > 0:
            return path
        return None

    def rotate_log(self, artifact_root: Path, *, session_index: int) -> Path | None:
        active = self.log_path(artifact_root)
        if not active.is_file():
            active.parent.mkdir(parents=True, exist_ok=True)
            active.write_text("", encoding="utf-8")
            return None
        if _count_nonempty_lines(active) == 0:
            active.write_text("", encoding="utf-8")
            return None
        archived = self.session_archive_path(artifact_root, session_index)
        shutil.copy2(active, archived)
        active.write_text("", encoding="utf-8")
        logger.info("[%s] 已归档会话副本 %s", self.service_name, archived.name)
        return archived

    def bootstrap_log(self, adb: AdbService, artifact_root: Path) -> Path:
        path = self.log_path(artifact_root)
        lines = self.fetch_device_lines(adb)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
        logger.info("[%s] 初始快照已写入 %s | %d 行", self.service_name, path, len(lines))
        return path

    def append_line(self, path: Path, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(stripped + "\n")

    def append_stage_marker(self, artifact_root: Path, phase: str, note: str = "") -> None:
        root = resolve_pipeline_artifact_root(artifact_root)
        path = self.log_path(root)
        phase = (phase or "-").strip()
        text = f"# [STAGE:{phase}]"
        if note.strip():
            text = f"{text} {note.strip()}"
        marker_key = self.log_dedup_key(text)
        if marker_key in self.read_dedup_keys(path):
            return
        self.append_line(path, text)

    def tail_log_lines(
        self,
        artifact_root: Path,
        adb: AdbService | None = None,
        *,
        limit: int = 100,
        refresh_from_device: bool = True,
    ) -> tuple[list[str], Path]:
        root = resolve_pipeline_artifact_root(artifact_root)
        path = self.log_path(root)
        if refresh_from_device and adb is not None:
            seen_keys = self.read_dedup_keys(path)
            dump_lines = self.fetch_device_lines(adb)
            if dump_lines:
                self.append_unique_lines(path, dump_lines, seen_keys)
        if not path.is_file():
            return [], path
        lines = [
            line
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        if limit > 0 and len(lines) > limit:
            lines = lines[-limit:]
        return lines, path

    def assess_health(self, log_text: str, *, ui_stage: str = "") -> LogHealthVerdict:
        return LogHealthVerdict(False, "", ())

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
                f"No {self.service_name} log lines yet ({path.name} missing or empty). "
                "Parallel log monitor may still be starting."
            )
        header = f"Latest {len(lines)} {self.service_name} log lines from {path.name}:\n"
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

    def finalize_log(self, adb: AdbService, artifact_root: Path) -> Path | None:
        path = self.log_path(artifact_root)
        seen_keys = self.read_dedup_keys(path)
        dump_lines = self.fetch_device_lines(adb)
        if not seen_keys and dump_lines:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(dump_lines) + "\n", encoding="utf-8")
        else:
            added = self.append_unique_lines(path, dump_lines, seen_keys)
            if added:
                logger.info("[%s] 收尾追加设备缓冲区 %d 行", self.service_name, added)
        self.merge_session_archives(artifact_root)
        path = self.ensure_log_for_analysis(artifact_root)
        if path is None:
            logger.warning("[%s] 未收集到日志: %s", self.service_name, self.log_path(artifact_root))
            return None
        logger.info("[%s] 归档完成 %s | 共 %d 行", self.service_name, self.log_filename, _count_nonempty_lines(path))
        return path
