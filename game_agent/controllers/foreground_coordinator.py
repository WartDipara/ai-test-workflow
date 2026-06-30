from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from game_agent.models.settings import AppConfig
from game_agent.modules.run_context import AttemptContext
from game_agent.services.adb_service import AdbService
from game_agent.services.run_audit_log import RunAuditLogger

if TYPE_CHECKING:
    from game_agent.models.task_config import TaskConfig

logger = logging.getLogger(__name__)


def _target_package(app_config: AppConfig | TaskConfig) -> str:
    return (getattr(app_config.game, "package_name", None) or "").strip()


def _launch_activity(app_config: AppConfig | TaskConfig) -> str | None:
    activity = (getattr(app_config.game, "launch_activity", None) or "").strip()
    return activity or None


def _allowed_foreground_packages(app_config: AppConfig) -> set[str]:
    return {
        p.strip()
        for p in app_config.foreground_guard.allowed_foreground_packages
        if (p or "").strip()
    }


def _is_acceptable_foreground(
    fg_pkg: str | None,
    *,
    target_pkg: str,
    allowlist: set[str],
) -> bool:
    if not fg_pkg:
        return False
    if fg_pkg == target_pkg:
        return True
    return fg_pkg in allowlist


@dataclass(slots=True)
class ForegroundCoordinator:
    """轮询前台包名；失焦时暂停 executor 并尝试 am start 切回目标游戏。"""

    adb: AdbService
    app_config: AppConfig | TaskConfig
    attempt_context: AttemptContext
    audit: RunAuditLogger | None = None
    _recovery_count: int = field(default=0, init=False, repr=False)

    async def run_until_fatal(self, stop_event: asyncio.Event) -> str | None:
        cfg = self.app_config.foreground_guard
        if not cfg.enabled:
            return None

        target_pkg = _target_package(self.app_config)
        if not target_pkg:
            logger.warning("[ForegroundGuard] No target package, skip foreground guard")
            return None

        logger.info(
            "[ForegroundGuard] 已启动 poll=%.1fs max_recoveries=%d target=%s",
            cfg.poll_interval_s,
            cfg.max_recoveries,
            target_pkg,
        )

        while not stop_event.is_set():
            msg = await asyncio.to_thread(self._poll_and_recover_once, target_pkg)
            if msg:
                return msg

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=cfg.poll_interval_s,
                )
            except TimeoutError:
                continue

        return None

    def _poll_and_recover_once(self, target_pkg: str) -> str | None:
        cfg = self.app_config.foreground_guard
        allowlist = _allowed_foreground_packages(self.app_config)
        fg_pkg, fg_act = self.adb.current_foreground_app()

        if _is_acceptable_foreground(fg_pkg, target_pkg=target_pkg, allowlist=allowlist):
            if self.attempt_context.is_foreground_lost():
                logger.info(
                    "[ForegroundGuard] 前台已恢复 target=%s fg=%s/%s",
                    target_pkg,
                    fg_pkg,
                    fg_act or "",
                )
            self.attempt_context.set_foreground_lost(False)
            self._recovery_count = 0
            return None

        if not fg_pkg:
            logger.debug("[ForegroundGuard] Foreground parse failed, skip round")
            return None

        self.attempt_context.set_foreground_lost(True)
        logger.warning(
            "[ForegroundGuard] 前台丢失 target=%s foreground=%s/%s recoveries=%d/%d",
            target_pkg,
            fg_pkg,
            fg_act or "",
            self._recovery_count,
            cfg.max_recoveries,
        )
        if self.audit is not None:
            self.audit.log_observer(
                kind="foreground_lost",
                message=f"target={target_pkg} foreground={fg_pkg}",
                extra={"foreground_activity": fg_act or ""},
            )

        activity = _launch_activity(self.app_config)
        try:
            out = self.adb.launch_game(target_pkg, activity)
            logger.info("[ForegroundGuard] launch_game: %s", (out or "")[:300])
        except Exception as exc:
            logger.warning("[ForegroundGuard] launch_game failed: %s", exc)
            self._recovery_count += 1
            return self._maybe_fatal(target_pkg, fg_pkg, str(exc))

        time.sleep(max(0.5, cfg.recover_verify_delay_s))
        verify_pkg, verify_act = self.adb.current_foreground_app()
        if _is_acceptable_foreground(
            verify_pkg,
            target_pkg=target_pkg,
            allowlist=allowlist,
        ):
            logger.info(
                "[ForegroundGuard] 切回成功 target=%s fg=%s/%s",
                target_pkg,
                verify_pkg,
                verify_act or "",
            )
            self.attempt_context.set_foreground_lost(False)
            self._recovery_count = 0
            if self.audit is not None:
                self.audit.log_observer(
                    kind="foreground_recovered",
                    message=f"target={target_pkg} foreground={verify_pkg}",
                )
            return None

        self._recovery_count += 1
        logger.warning(
            "[ForegroundGuard] 切回验证失败 target=%s foreground=%s/%s",
            target_pkg,
            verify_pkg or fg_pkg,
            verify_act or fg_act or "",
        )
        return self._maybe_fatal(target_pkg, verify_pkg or fg_pkg, "verify failed")

    def _maybe_fatal(self, target_pkg: str, foreground_pkg: str, detail: str) -> str | None:
        cfg = self.app_config.foreground_guard
        if self._recovery_count < cfg.max_recoveries:
            return None
        msg = (
            f"前台应用丢失: foreground recover failed after {self._recovery_count} attempts "
            f"(foreground={foreground_pkg}, target={target_pkg}, detail={detail})"
        )
        logger.error("[ForegroundGuard] %s", msg)
        self.attempt_context.signal_fatal(msg)
        return msg
