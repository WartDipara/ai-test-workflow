from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from game_agent.models.settings import AppConfig
from game_agent.modules.observer_session.state import ObserverSessionState
from game_agent.services.adb_service import AdbService
from game_agent.services.gameturbo_log import (
    append_gameturbo_line,
    bootstrap_gameturbo_log,
    gameturbo_log_dedup_key,
    gameturbo_log_path,
    read_gameturbo_dedup_keys,
)
from game_agent.services.run_audit_log import RunAuditLogger

logger = logging.getLogger(__name__)

_ANOMALY_MARKERS = (
    "channel closed",
    "tunnel closed",
    "shutdown",
    "idle shutdown: no streams for 300s, closing tunnel",
)


@dataclass(slots=True)
class LogMonitor:
    """GameTurbo logcat 实时监控模块。"""

    adb: AdbService
    app_config: AppConfig
    artifact_root: Path
    session_state: ObserverSessionState | None = None
    audit: RunAuditLogger | None = None
    _restart_requested: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    async def restart_session(self) -> None:
        """会话重启：请求终止当前 logcat 流，外层循环将重新 bootstrap。"""
        self._restart_requested.set()

    async def run_until_anomaly(self, stop_event: asyncio.Event) -> str | None:
        logger.info("[LogMonitor] 开始监控 GameTurbo 日志...")
        log_path = gameturbo_log_path(self.artifact_root)
        bootstrap_gameturbo_log(self.adb, self.artifact_root)
        seen_keys = read_gameturbo_dedup_keys(log_path)

        while not stop_event.is_set():
            if self._restart_requested.is_set():
                self._restart_requested.clear()
                log_path = gameturbo_log_path(self.artifact_root)
                seen_keys = read_gameturbo_dedup_keys(log_path)
                logger.info("[LogMonitor] 会话重启后恢复 logcat 流")

            anomaly = await self._run_one_stream(
                stop_event,
                log_path=log_path,
                seen_keys=seen_keys,
            )
            if anomaly is not None:
                return anomaly
            if stop_event.is_set():
                break
            if not self._restart_requested.is_set():
                break

        return None

    async def _run_one_stream(
        self,
        stop_event: asyncio.Event,
        *,
        log_path: Path,
        seen_keys: set[str],
    ) -> str | None:
        cmd = self.adb._base() + ["logcat", "-s", "GameTurbo"]
        logger.info("[LogMonitor] 监听命令: %s", " ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        line_count = 0
        last_heartbeat = time.monotonic()
        heartbeat_interval_s = 30.0

        try:
            while not stop_event.is_set() and not self._restart_requested.is_set():
                if process.stdout is None:
                    break
                try:
                    line_bytes = await asyncio.wait_for(
                        process.stdout.readline(),
                        timeout=1.0,
                    )
                except TimeoutError:
                    now = time.monotonic()
                    if now - last_heartbeat >= heartbeat_interval_s:
                        logger.info(
                            "[LogMonitor] 仍在监听 GameTurbo | 已收 %d 条 | session=%d",
                            line_count,
                            self.session_state.session_index
                            if self.session_state
                            else 1,
                        )
                        last_heartbeat = now
                    continue
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                line_count += 1
                key = gameturbo_log_dedup_key(line)
                if key not in seen_keys:
                    append_gameturbo_line(log_path, line)
                    seen_keys.add(key)
                if line_count <= 3 or line_count % 20 == 0:
                    logger.info("[LogMonitor] logcat #%d: %s", line_count, line[:200])
                lower = line.lower()
                if any(m in lower for m in _ANOMALY_MARKERS):
                    logger.warning("[LogMonitor] 检测到异常日志: %s", line)
                    if self.audit is not None:
                        self.audit.log_observer(
                            kind="log_anomaly",
                            message=line,
                            extra={"marker": "matched"},
                        )
                    return f"Log anomaly detected: {line}"
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except TimeoutError:
                    process.kill()
        return None
