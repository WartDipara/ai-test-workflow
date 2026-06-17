from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from game_agent.models.settings import AppConfig
from game_agent.services.adb_service import AdbService
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.services.vision_tools import is_network_anomaly_reason
from game_agent.utils.ocr_util import extract_text_with_bounds
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StabilityCheckResult:
    has_fatal_anomaly: bool
    loading_ok: bool
    stage: str
    reason: str
    screenshot_path: Path


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```json"):
        s = s[7:]
    if s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def _parse_stability_raw(raw: str) -> dict:
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


async def run_stability_check(
    *,
    adb: AdbService,
    cfg: AppConfig,
    artifact_root: Path,
    round_id: int,
    audit: RunAuditLogger | None = None,
) -> StabilityCheckResult:
    """截图 + OCR + 多模态稳定性判定。"""
    from datetime import datetime

    llm_cfg = cfg.llm_multimodal
    ts = datetime.now().strftime("%H%M%S_%f")
    shot_path = artifact_root / f"stability_observe_{round_id:03d}_{ts}.png"
    adb.screencap_png(shot_path)
    try:
        dw, dh = adb.touch_size()
        ocr_summary = extract_text_with_bounds(shot_path, device_w=dw, device_h=dh)
    except Exception as e:
        ocr_summary = f"[OCR failed] {e}"

    if llm_cfg is None:
        return StabilityCheckResult(
            has_fatal_anomaly=False,
            loading_ok=True,
            stage="unknown",
            reason="no multimodal config",
            screenshot_path=shot_path,
        )

    vision = VisionWorker(llm_cfg)
    raw = await vision.judge_in_game_stability(
        screenshot_path=shot_path,
        ocr_summary=ocr_summary,
        round_id=round_id,
    )
    data = _parse_stability_raw(raw)
    anomaly_reason = str(data.get("anomaly_reason", "") or "")
    reason = str(data.get("reason", "") or anomaly_reason or "")
    has_fatal = bool(data.get("has_fatal_anomaly"))
    if not has_fatal and anomaly_reason and is_network_anomaly_reason(anomaly_reason):
        has_fatal = True
    loading_ok = bool(data.get("loading_ok", True))
    if not loading_ok and not has_fatal:
        has_fatal = True
        reason = reason or "loading not ok"
    stage = str(data.get("stage", "unknown") or "unknown")

    if audit is not None:
        audit.log_observer(
            kind="stability_observe",
            message=reason[:500],
            round_id=round_id,
            extra={
                **data,
                "has_fatal_anomaly": has_fatal,
                "screenshot": str(shot_path),
            },
        )

    logger.info(
        "[stability_observe] round=%d fatal=%s loading_ok=%s stage=%s | %s",
        round_id,
        has_fatal,
        loading_ok,
        stage,
        reason[:200],
    )
    return StabilityCheckResult(
        has_fatal_anomaly=has_fatal,
        loading_ok=loading_ok,
        stage=stage,
        reason=reason,
        screenshot_path=shot_path,
    )
