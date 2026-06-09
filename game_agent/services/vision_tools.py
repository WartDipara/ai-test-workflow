from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from game_agent.models.settings import AppConfig
from game_agent.models.vision_tool_result import VisionToolErrorCode, format_vision_tool_response
from game_agent.modules.run_context import AttemptContext
from game_agent.services.adb_service import AdbService
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.utils.ocr_util import extract_text_with_bounds
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)

_NETWORK_ANOMALY_HINTS = (
    "网络连接失败",
    "网络异常",
    "网络无连接",
    "没有网络",
    "请检查网络",
    "连接超时",
    "连接失败",
    "服务器连接失败",
    "与服务器断开连接",
    "服务器加载失败",
    "服务器获取失败",
    "服务器繁忙",
    "服务器维护中",
    "资源下载失败",
    "资源加载失败",
    "更新失败",
    "下载失败",
    "当前地区不支持",
    "当前区域暂未开放",
)


def is_network_anomaly_reason(reason: str) -> bool:
    return any(k in (reason or "") for k in _NETWORK_ANOMALY_HINTS)


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```json"):
        s = s[7:]
    if s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


async def run_analyze_screen(
    *,
    adb: AdbService,
    cfg: AppConfig,
    artifact_root: Path,
    round_id: int,
    reason: str = "",
    attempt_context: AttemptContext | None = None,
    audit: RunAuditLogger | None = None,
) -> str:
    """
    主脑按需：截图 + OCR + 多模态画面分析（原 ScreenMonitor 单轮逻辑）。
    不 fail-fast 整局；由主脑根据 data.has_anomaly / stage 决定下一步。
    """
    llm_cfg = cfg.llm_multimodal
    if llm_cfg is None:
        return format_vision_tool_response(
            error_code=VisionToolErrorCode.NO_MULTIMODAL,
            error_message="llm_multimodal 未配置，无法调用多模态",
        )

    ts = datetime.now().strftime("%H%M%S_%f")
    shot_path = artifact_root / f"analyze_screen_{round_id:03d}_{ts}.png"
    try:
        adb.screencap_png(shot_path)
    except Exception as e:
        return format_vision_tool_response(
            error_code=VisionToolErrorCode.API_ERROR,
            error_message=f"screencap failed: {e}",
        )

    try:
        dw, dh = adb.touch_size()
        ocr_summary = extract_text_with_bounds(shot_path, device_w=dw, device_h=dh)
    except Exception as e:
        ocr_summary = f"[OCR failed] {e}"
        logger.warning("analyze_screen OCR 失败: %s", e)

    vision = VisionWorker(llm_cfg)
    try:
        raw = await vision.analyze_game_state(
            screenshot_path=shot_path,
            ocr_summary=ocr_summary,
            round_id=round_id,
        )
    except Exception as e:
        logger.exception("analyze_screen 多模态 API 失败")
        return format_vision_tool_response(
            error_code=VisionToolErrorCode.API_ERROR,
            error_message=str(e)[:800],
            data={"screenshot": str(shot_path), "ocr_preview": ocr_summary[:500]},
        )

    try:
        state = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError as e:
        return format_vision_tool_response(
            error_code=VisionToolErrorCode.PARSE_ERROR,
            error_message=f"invalid JSON from vision model: {e}",
            data={"raw_preview": raw[:800], "screenshot": str(shot_path)},
        )

    if not isinstance(state, dict):
        return format_vision_tool_response(
            error_code=VisionToolErrorCode.PARSE_ERROR,
            error_message="vision model JSON is not an object",
            data={"raw_preview": raw[:800]},
        )

    stage = str(state.get("stage", "unknown"))
    progress = str(state.get("progress", "") or "")
    if attempt_context is not None:
        attempt_context.set_ui_observation(stage, progress)

    anomaly_reason = str(state.get("anomaly_reason", "") or "")
    if audit is not None:
        audit.log_observer(
            kind="analyze_screen",
            message=reason[:300] or stage,
            round_id=round_id,
            extra={**state, "screenshot": str(shot_path)},
        )

    data = {
        **state,
        "screenshot": str(shot_path),
        "ocr_preview": ocr_summary[:1500],
        "ocr_coord_space": "adb_touch_size_tap_ready",
        "request_reason": (reason or "")[:300],
        "network_anomaly_hint": is_network_anomaly_reason(anomaly_reason),
    }
    return format_vision_tool_response(
        error_code=VisionToolErrorCode.OK,
        data=data,
    )
