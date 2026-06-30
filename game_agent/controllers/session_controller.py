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
from game_agent.services.external_log_base import ExternalLogCollector
from game_agent.services.game_launch import (
    get_package_pids,
    package_primary_pid_changed,
    primary_package_pid,
)
from game_agent.services.run_audit_log import RunAuditLogger

if TYPE_CHECKING:
    from game_agent.controllers.log_monitor_controller import LogMonitor

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SessionCoordinator:
    """Detect game process crash/restart, archive logs, reset monitor state."""

    adb: AdbService
    app_config: AppConfig
    artifact_root: Path
    session_state: ObserverSessionState
    audit: RunAuditLogger | None = None
    log_monitor: LogMonitor | None = None
    attempt_context: AttemptContext | None = None
    log_collector: ExternalLogCollector | None = None

    async def watch(self, stop_event: asyncio.Event) -> str | None:
        """
        Poll game process; absent then reappeared triggers session restart.
        Non-None return means abort observer (e.g. max_session_restarts exceeded).
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
            "[SessionCoordinator] Watching process restarts | pkg=%s | "
            "poll=%.1fs | absent_threshold=%.1fs",
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
                                f"Game process absent {absent_duration:.1f}s then reappeared "
                                f"(pids={sorted(pids)})"
                            ),
                        )
                        if fail is not None:
                            return fail
                    absent_since = None

                if (
                    had_running
                    and last_pids
                    and absent_since is None
                    and package_primary_pid_changed(last_pids, pids)
                ):
                    fail = await self._on_session_restart(
                        reason=(
                            f"Primary game process pid changed "
                            f"{primary_package_pid(last_pids)} -> {primary_package_pid(pids)} "
                            f"(all_pids {sorted(last_pids)} -> {sorted(pids)})"
                        ),
                    )
                    if fail is not None:
                        return fail

                had_running = True
                last_pids = pids
            else:
                if had_running and absent_since is None:
                    absent_since = now
                    logger.info("[SessionCoordinator] Game process gone, starting timer")
                    if self.attempt_context is not None:
                        self.attempt_context.bump_session_generation(
                            reason="process_absent:invalidate_inflight",
                        )
                last_pids = frozenset()

            if (
                max_restarts > 0
                and self.session_state.restarts_count >= max_restarts
            ):
                msg = (
                    f"Session restart limit reached ({max_restarts}), aborting observer"
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
            self.attempt_context.bump_session_generation(
                reason=f"session_restart:{reason[:120]}",
            )
            self.attempt_context.request_session_relogin_recovery()
            self.attempt_context.set_session_restarts(self.session_state.restarts_count)
            self.attempt_context.set_session_index(self.session_state.session_index)

        logger.warning(
            "[SessionCoordinator] Session restart #%d | %s",
            self.session_state.session_index,
            reason[:300],
        )

        archived = (
            self.log_collector.rotate_log(self.artifact_root, session_index=idx_before)
            if self.log_collector is not None
            else None
        )
        if archived is not None:
            logger.info("[SessionCoordinator] Archived log %s", archived.name)

        if cfg.game.clear_logcat_on_session_restart and self.log_collector is not None:
            try:
                self.log_collector.clear_device_logcat(self.adb)
            except Exception as e:
                logger.warning("[SessionCoordinator] logcat -c failed: %s", e)

        if self.log_collector is not None:
            self.log_collector.bootstrap_log(self.adb, self.artifact_root)

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
                f"Session restart limit reached ({max_restarts}): {reason[:200]}"
            )
        return None
