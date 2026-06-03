from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from game_agent.models.settings import AppConfig
from game_agent.modules.observer_session.state import ObserverSessionState
from game_agent.modules.run_context import AttemptContext
from game_agent.services.adb_service import AdbService
from game_agent.services.game_launch import get_package_pids
from game_agent.services.gameturbo_log import (
    bootstrap_gameturbo_log,
    clear_device_logcat,
    rotate_gameturbo_log,
)
from game_agent.services.run_audit_log import RunAuditLogger

if TYPE_CHECKING:
    from game_agent.controllers.log_monitor_controller import LogMonitor

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SessionCoordinator:
    """检测游戏进程 crash/重启，归档日志并通知各监控模块重置状态。"""

    adb: AdbService
    app_config: AppConfig
    artifact_root: Path
    session_state: ObserverSessionState
    audit: RunAuditLogger | None = None
    log_monitor: LogMonitor | None = None
    attempt_context: AttemptContext | None = None

    async def watch(self, stop_event: asyncio.Event) -> str | None:
        """
        轮询游戏进程；检测到消失后再现则触发会话重启。
        返回非 None 表示应中止观察者（如超过 max_session_restarts）。
        """
        cfg = self.app_config
        game_pkg = cfg.game.package_name
        poll_s = cfg.game.session_poll_interval_s
        absent_threshold = cfg.game.session_absent_threshold_s
        max_restarts = cfg.game.max_session_restarts

        had_running = False
        last_pids: frozenset[str] = frozenset()
        absent_since: float | None = None

        logger.info(
            "[SessionCoordinator] 开始监听进程重启 | 包=%s | 轮询=%.1fs | 缺失阈值=%.1fs",
            game_pkg,
            poll_s,
            absent_threshold,
        )

        while not stop_event.is_set() and self.session_state.monitoring_enabled:
            pids = frozenset(get_package_pids(self.adb, game_pkg))
            running = bool(pids)
            now = time.monotonic()

            if running:
                if had_running and absent_since is not None:
                    absent_duration = now - absent_since
                    if absent_duration >= absent_threshold:
                        fail = await self._on_session_restart(
                            reason=(
                                f"游戏进程消失 {absent_duration:.1f}s 后重新出现 "
                                f"(pids={sorted(pids)})"
                            ),
                        )
                        if fail is not None:
                            return fail
                    absent_since = None

                if had_running and last_pids and pids != last_pids and absent_since is None:
                    fail = await self._on_session_restart(
                        reason=f"游戏进程 pid 变化 {sorted(last_pids)} -> {sorted(pids)}",
                    )
                    if fail is not None:
                        return fail

                had_running = True
                last_pids = pids
            else:
                if had_running and absent_since is None:
                    absent_since = now
                    logger.info("[SessionCoordinator] 检测到游戏进程消失，开始计时")
                last_pids = frozenset()

            if (
                max_restarts > 0
                and self.session_state.restarts_count >= max_restarts
            ):
                msg = (
                    f"游戏会话重启次数达到上限 ({max_restarts})，中止本轮观察者"
                )
                logger.warning("[SessionCoordinator] %s", msg)
                return msg

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_s)
            except TimeoutError:
                continue

        return None

    async def _on_session_restart(self, *, reason: str) -> str | None:
        cfg = self.app_config
        idx_before = self.session_state.session_index
        self.session_state.reset_for_new_session(reason=reason)
        if self.attempt_context is not None:
            self.attempt_context.request_reset_in_game_streak()
            self.attempt_context.set_session_restarts(self.session_state.restarts_count)

        logger.warning(
            "[SessionCoordinator] 会话重启 #%d | %s",
            self.session_state.session_index,
            reason[:300],
        )

        archived = rotate_gameturbo_log(self.artifact_root, session_index=idx_before)
        if archived is not None:
            logger.info("[SessionCoordinator] 已归档日志 %s", archived.name)

        if cfg.game.clear_logcat_on_session_restart:
            try:
                clear_device_logcat(self.adb)
            except Exception as e:
                logger.warning("[SessionCoordinator] logcat -c 失败: %s", e)

        bootstrap_gameturbo_log(self.adb, self.artifact_root)

        if self.log_monitor is not None:
            await self.log_monitor.restart_session()

        if self.audit is not None:
            self.audit.log_phase(
                "session_restart",
                reason[:500],
                session_index=self.session_state.session_index,
                restarts_count=self.session_state.restarts_count,
                archived_log=str(archived) if archived else None,
            )

        max_restarts = cfg.game.max_session_restarts
        if max_restarts > 0 and self.session_state.restarts_count >= max_restarts:
            return (
                f"游戏会话重启次数达到上限 ({max_restarts}): {reason[:200]}"
            )
        return None
