from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from game_agent.models.settings import AppConfig
from game_agent.modules.run_context import AttemptContext
from game_agent.services.adb_service import AdbService
from game_agent.services.anomaly_evidence import write_anomaly_evidence
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.services.screen_download_health import (
    ScreenProgressTracker,
    detect_network_dialog_in_ocr,
    parse_download_percent_from_ocr,
    parse_percent_from_progress_text,
)
from game_agent.utils.ocr_util import run_ocr_frame

logger = logging.getLogger(__name__)


def format_confirmed_network_anomaly(
    *,
    log_reason: str,
    screen_reason: str,
    ui_stage: str = "",
) -> str:
    """兼容旧测试：双通道日志+画面格式。"""
    stage_note = f" ui_stage={ui_stage}" if ui_stage else ""
    return (
        "Observer network anomaly confirmed (log + screen):"
        f"{stage_note} log={log_reason[:400]} | screen={screen_reason[:400]}"
    )


def format_confirmed_vision_ocr_anomaly(
    *,
    ocr_reason: str,
    vision_reason: str = "",
    ui_stage: str = "",
) -> str:
    stage_note = f" ui_stage={ui_stage}" if ui_stage else ""
    vision_note = f" vision={vision_reason[:400]}" if vision_reason else ""
    return (
        f"Vision/OCR network anomaly confirmed:{stage_note}"
        f" ocr={ocr_reason[:400]}{vision_note}"
    )


def _parse_vision_anomaly(vision_raw: str) -> tuple[bool, str, str]:
    text = (vision_raw or "").strip()
    if not text:
        return False, "", ""
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False, "", ""
    has_anomaly = bool(data.get("has_anomaly", False))
    stage = str(data.get("stage", "") or "")
    reason = str(data.get("anomaly_reason", "") or data.get("message", "") or "")
    return has_anomaly, stage, reason


@dataclass(slots=True)
class NetworkAnomalyCoordinator:
    """
    OCR + 多模态网络异常监视（运行期不读日志规则）。
    OCR suspect 后由多模态确认（可配置）；高置信 OCR 网络弹窗可在跳过多模态时直接 fatal。
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
        logger.info(
            "[NetworkAnomaly] OCR+多模态监视已启动 poll=%.1fs stall=%.0fs multimodal_confirm=%s",
            cfg.poll_interval_s,
            cfg.download_progress_stall_s,
            cfg.require_multimodal_confirm,
        )

        while not stop_event.is_set():
            confirmed = await self._poll_once(tracker)
            if confirmed is not None:
                return confirmed

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=cfg.poll_interval_s,
                )
            except TimeoutError:
                continue

        return None

    async def _poll_once(self, tracker: ScreenProgressTracker) -> str | None:
        cfg = self.app_config.network_anomaly
        stage, progress = "", ""
        if self.attempt_context is not None:
            stage, progress = self.attempt_context.get_ui_observation()

        ocr_summary = ""
        dialog_hint = ""
        shot_path = ""
        percent = parse_percent_from_progress_text(progress)

        if cfg.use_ocr_poll:
            if self.attempt_context is not None and self.attempt_context.is_ocr_busy():
                logger.debug("[NetworkAnomaly] skip OCR poll — executor busy")
            else:
                ocr_summary, dialog_hint, shot_path = await asyncio.to_thread(
                    self._capture_ocr_summary,
                )
                if percent is None and ocr_summary:
                    dw, dh = self.adb.touch_size()
                    percent = parse_download_percent_from_ocr(
                        ocr_summary,
                        screen_h=dh,
                        min_y_ratio=cfg.exclude_top_ratio,
                    )

        ocr_suspect = False
        ocr_reason = ""

        if dialog_hint:
            ocr_suspect = True
            ocr_reason = f"network dialog on screen: {dialog_hint}"

        effective_stage = stage or "unknown"
        stall_percent: int | None = None
        if effective_stage in ("resource_download", "loading"):
            if percent is not None and not progress:
                progress = f"{percent}%"
                if self.attempt_context is not None:
                    self.attempt_context.set_ui_observation(effective_stage, progress)
            stall_percent = percent

        stall_verdict = tracker.observe(
            stage=effective_stage,
            progress=progress,
            percent=stall_percent,
            stall_s=cfg.download_progress_stall_s,
        )
        if stall_verdict.suspect:
            ocr_suspect = True
            ocr_reason = stall_verdict.reason or ocr_reason

        if not ocr_suspect:
            return None

        vision_raw = ""
        vision_has_anomaly = False
        vision_stage = ""
        vision_reason = ""

        need_vision = cfg.require_multimodal_confirm or not dialog_hint
        llm_mm = self.app_config.llm_multimodal

        if need_vision and llm_mm is not None and shot_path:
            from game_agent.workers.vision_worker import VisionWorker

            vision = VisionWorker(llm_mm)
            try:
                vision_raw = await vision.analyze_game_state(
                    screenshot_path=Path(shot_path),
                    ocr_summary=ocr_summary,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[NetworkAnomaly] multimodal confirm failed: %s", e)
            else:
                vision_has_anomaly, vision_stage, vision_reason = _parse_vision_anomaly(
                    vision_raw,
                )

        confirmed = False
        if cfg.require_multimodal_confirm:
            if vision_has_anomaly:
                confirmed = True
            elif dialog_hint and not llm_mm:
                confirmed = True
        else:
            confirmed = bool(dialog_hint or vision_has_anomaly or stall_verdict.suspect)

        if not confirmed:
            logger.info(
                "[NetworkAnomaly] OCR suspect 未获多模态确认: %s",
                ocr_reason[:200],
            )
            if self.audit is not None:
                self.audit.log_observer(
                    kind="screen_suspect",
                    message=ocr_reason[:500],
                    extra={"vision_has_anomaly": vision_has_anomaly},
                )
            return None

        ui_stage, _ = (
            self.attempt_context.get_ui_observation()
            if self.attempt_context is not None
            else ("", "")
        )
        msg = format_confirmed_vision_ocr_anomaly(
            ocr_reason=ocr_reason,
            vision_reason=vision_reason or vision_stage,
            ui_stage=ui_stage,
        )
        write_anomaly_evidence(
            self.artifact_root,
            fatal_message=msg,
            ocr_reason=ocr_reason,
            ocr_summary=ocr_summary,
            screenshot_path=shot_path,
            vision_raw=vision_raw,
            vision_has_anomaly=vision_has_anomaly,
            vision_stage=vision_stage,
            ui_stage=ui_stage,
        )
        logger.warning("[NetworkAnomaly] 已确认: %s", msg[:500])
        if self.audit is not None:
            self.audit.log_observer(
                kind="network_anomaly_confirmed",
                message=msg[:2000],
                extra={
                    "ocr_reason": ocr_reason[:500],
                    "vision_has_anomaly": vision_has_anomaly,
                    "vision_stage": vision_stage,
                },
            )
        if self.attempt_context is not None:
            self.attempt_context.signal_fatal(msg)
        return msg

    def _capture_ocr_summary(self) -> tuple[str, str, str]:
        cfg = self.app_config.network_anomaly
        ts = datetime.now().strftime("%H%M%S_%f")
        shot = self.artifact_root / f"net_watch_{ts}.png"
        try:
            self.adb.screencap_png(shot)
            dw, dh = self.adb.touch_size()
            ocr_summary, _ = run_ocr_frame(
                shot,
                device_w=dw,
                device_h=dh,
                worker_key=self.adb.device_serial,
            )
        except Exception as e:
            logger.debug("[NetworkAnomaly] OCR poll failed: %s", e)
            return "", "", ""
        dialog = detect_network_dialog_in_ocr(
            ocr_summary,
            min_y_ratio=cfg.exclude_top_ratio,
        )
        return ocr_summary, dialog, str(shot.resolve())
