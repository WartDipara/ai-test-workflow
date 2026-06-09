from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from game_agent.models.settings import AppConfig
from game_agent.modules.run_context import AttemptContext
from game_agent.services.adb_service import AdbService
from game_agent.services.gameturbo_log import gameturbo_log_path
from game_agent.services.gameturbo_log_health import assess_gameturbo_log_health
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.services.screen_download_health import (
    ScreenProgressTracker,
    detect_network_dialog_in_ocr,
    parse_download_percent_from_ocr,
    parse_percent_from_progress_text,
)
from game_agent.utils.ocr_util import extract_text_with_bounds

logger = logging.getLogger(__name__)


def format_confirmed_network_anomaly(
    *,
    log_reason: str,
    screen_reason: str,
) -> str:
    return (
        "Network anomaly confirmed (log + screen): "
        f"log={log_reason[:400]} | screen={screen_reason[:400]}"
    )


@dataclass(slots=True)
class NetworkAnomalyCoordinator:
    """
    日志 / 画面双通道 50-50 监视：
    任一侧 suspect 时由另一侧佐证，两侧均认可才 fail-fast。
    """

    adb: AdbService
    app_config: AppConfig
    artifact_root: Path
    attempt_context: AttemptContext | None = None
    audit: RunAuditLogger | None = None

    async def run_until_confirmed(self, stop_event: asyncio.Event) -> str | None:
        cfg = self.app_config.network_anomaly
        if not cfg.enabled:
            return None

        tracker = ScreenProgressTracker()
        log_path = gameturbo_log_path(self.artifact_root)
        logger.info(
            "[NetworkAnomaly] 双通道监视已启动 poll=%.1fs stall=%.0fs",
            cfg.poll_interval_s,
            cfg.download_progress_stall_s,
        )

        while not stop_event.is_set():
            log_verdict = self._assess_log_channel(log_path)
            screen_verdict = await self._assess_screen_channel(tracker)

            if log_verdict.suspect and screen_verdict.suspect:
                msg = format_confirmed_network_anomaly(
                    log_reason=log_verdict.reason,
                    screen_reason=screen_verdict.reason,
                )
                logger.warning("[NetworkAnomaly] 双通道确认: %s", msg[:500])
                if self.audit is not None:
                    self.audit.log_observer(
                        kind="network_anomaly_confirmed",
                        message=msg[:2000],
                        extra={
                            "log_markers": list(log_verdict.markers),
                            "screen_stage": screen_verdict.stage,
                            "screen_progress": screen_verdict.progress,
                        },
                    )
                if self.attempt_context is not None:
                    self.attempt_context.signal_fatal(msg)
                return msg

            if log_verdict.suspect:
                logger.info(
                    "[NetworkAnomaly] 日志通道 suspect，等待画面佐证: %s",
                    log_verdict.reason[:200],
                )
                if self.audit is not None:
                    self.audit.log_observer(
                        kind="log_suspect",
                        message=log_verdict.reason[:500],
                        extra={"markers": list(log_verdict.markers)},
                    )

            if screen_verdict.suspect:
                logger.info(
                    "[NetworkAnomaly] 画面通道 suspect，等待日志佐证: %s",
                    screen_verdict.reason[:200],
                )
                if self.audit is not None:
                    self.audit.log_observer(
                        kind="screen_suspect",
                        message=screen_verdict.reason[:500],
                        extra={
                            "stage": screen_verdict.stage,
                            "progress": screen_verdict.progress,
                        },
                    )

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=cfg.poll_interval_s,
                )
            except TimeoutError:
                continue

        return None

    def _assess_log_channel(self, log_path: Path):
        if not log_path.is_file():
            from game_agent.services.gameturbo_log_health import LogHealthVerdict

            return LogHealthVerdict(False, "", ())
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            from game_agent.services.gameturbo_log_health import LogHealthVerdict

            return LogHealthVerdict(False, f"log read error: {e}", ())
        return assess_gameturbo_log_health(
            text,
            min_lines=self.app_config.network_anomaly.min_log_lines,
        )

    async def _assess_screen_channel(self, tracker: ScreenProgressTracker):
        from game_agent.services.screen_download_health import ScreenHealthVerdict

        cfg = self.app_config.network_anomaly
        stage, progress = "", ""
        if self.attempt_context is not None:
            stage, progress = self.attempt_context.get_ui_observation()

        percent = parse_percent_from_progress_text(progress)
        ocr_summary = ""
        dialog_hint = ""

        if cfg.use_ocr_poll:
            ocr_summary, dialog_hint = await asyncio.to_thread(
                self._capture_ocr_summary,
            )
            if percent is None:
                dw, dh = self.adb.touch_size()
                percent = parse_download_percent_from_ocr(
                    ocr_summary,
                    screen_h=dh,
                    min_y_ratio=cfg.exclude_top_ratio,
                )
            if dialog_hint:
                return ScreenHealthVerdict(
                    True,
                    f"network dialog on screen: {dialog_hint}",
                    stage,
                    progress,
                )

        if percent is not None and not progress:
            progress = f"{percent}%"
            if self.attempt_context is not None:
                self.attempt_context.set_ui_observation(
                    stage or "resource_download",
                    progress,
                )

        return tracker.observe(
            stage=stage or ("resource_download" if percent is not None else "unknown"),
            progress=progress,
            percent=percent,
            stall_s=cfg.download_progress_stall_s,
        )

    def _capture_ocr_summary(self) -> tuple[str, str]:
        cfg = self.app_config.network_anomaly
        ts = datetime.now().strftime("%H%M%S_%f")
        shot = self.artifact_root / f"net_watch_{ts}.png"
        try:
            self.adb.screencap_png(shot)
            dw, dh = self.adb.touch_size()
            ocr_summary = extract_text_with_bounds(shot, device_w=dw, device_h=dh)
        except Exception as e:
            logger.debug("[NetworkAnomaly] OCR poll failed: %s", e)
            return "", ""
        dialog = detect_network_dialog_in_ocr(
            ocr_summary,
            min_y_ratio=cfg.exclude_top_ratio,
        )
        return ocr_summary, dialog
