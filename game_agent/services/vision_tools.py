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
from game_agent.utils.ocr_util import extract_text_with_bounds, run_ocr_frame
from game_agent.workers.vision_worker import VisionWorker

from game_agent.i18n.match import is_network_anomaly_text

logger = logging.getLogger(__name__)


def is_network_anomaly_reason(reason: str) -> bool:
    return is_network_anomaly_text(reason or "")


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
            error_message="llm_multimodal not configured",
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
        from game_agent.utils.screen_coord import resolve_screen_coord_space

        space = resolve_screen_coord_space(adb, shot_path)
        dw, dh = space.tap_w, space.tap_h
        ocr_summary = extract_text_with_bounds(shot_path, device_w=dw, device_h=dh)
    except Exception as e:
        ocr_summary = f"[OCR failed] {e}"
        logger.warning("analyze_screen OCR failed: %s", e)

    vision = VisionWorker(llm_cfg, attempt_context=attempt_context)
    from game_agent.modules.session_invalidation import capture_session_generation, discard_if_stale

    work_gen = capture_session_generation(attempt_context)
    try:
        raw = await vision.analyze_game_state(
            screenshot_path=shot_path,
            ocr_summary=ocr_summary,
            round_id=round_id,
        )
    except Exception as e:
        logger.exception("analyze_screen multimodal API failed")
        return format_vision_tool_response(
            error_code=VisionToolErrorCode.API_ERROR,
            error_message=str(e)[:800],
            data={"screenshot": str(shot_path), "ocr_preview": ocr_summary[:500]},
        )

    if discard_if_stale(work_gen, where="analyze_screen", ctx=attempt_context):
        return format_vision_tool_response(
            error_code=VisionToolErrorCode.API_ERROR,
            error_message="stale_session_discard",
            data={"screenshot": str(shot_path)},
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


async def run_dismiss_blank_modal(
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
    Tool: dismiss a tap-blank-to-close modal using OCR + geometry (no LLM coords).
    """
    ts = datetime.now().strftime("%H%M%S_%f")
    shot_path = artifact_root / f"dismiss_blank_{round_id:03d}_{ts}.png"
    try:
        adb.screencap_png(shot_path)
    except Exception as e:
        return format_vision_tool_response(
            error_code=VisionToolErrorCode.API_ERROR,
            error_message=f"screencap failed: {e}",
        )

    try:
        from game_agent.utils.screen_coord import resolve_screen_coord_space

        space = resolve_screen_coord_space(adb, shot_path)
        dw, dh = space.tap_w, space.tap_h
        ocr_summary, bboxes = run_ocr_frame(
            shot_path,
            device_w=dw,
            device_h=dh,
            worker_key=adb.device_serial,
        )
    except Exception as e:
        return format_vision_tool_response(
            error_code=VisionToolErrorCode.OCR_FAILED,
            error_message=str(e)[:500],
            data={"screenshot": str(shot_path)},
        )

    from game_agent.models.phase_template import PhaseSpec
    from game_agent.services.dismiss_blank_modal import (
        execute_dismiss_blank_modal,
        ocr_indicates_blank_dismiss,
        plan_blank_area_dismiss,
    )

    plan = plan_blank_area_dismiss(
        ocr_summary=ocr_summary,
        bboxes=bboxes,
        screen_w=dw,
        screen_h=dh,
    )
    spec = PhaseSpec(action="dismiss_blank", phase_id="dismiss_blank_tool")
    exec_msg, executed = execute_dismiss_blank_modal(
        spec,
        adb=adb,
        sw=dw,
        sh=dh,
        ocr_summary=ocr_summary,
        bboxes=bboxes,
    )
    if not executed:
        return format_vision_tool_response(
            error_code=VisionToolErrorCode.PARSE_ERROR,
            error_message=exec_msg,
            data={"screenshot": str(shot_path), "ocr_preview": ocr_summary[:800]},
        )

    adb.wait_seconds(0.8)
    after_path = artifact_root / f"dismiss_blank_after_{round_id:03d}_{ts}.png"
    adb.screencap_png(after_path)
    try:
        after_ocr, _ = run_ocr_frame(
            after_path,
            device_w=dw,
            device_h=dh,
            worker_key=adb.device_serial,
        )
    except Exception:
        after_ocr = ""

    hint_gone = plan is not None and not ocr_indicates_blank_dismiss(after_ocr)

    if audit is not None:
        audit.log_observer(
            kind="dismiss_blank_modal",
            message=reason[:200] or (plan.reason if plan else exec_msg[:120]),
            round_id=round_id,
            extra={
                "tap": [plan.x, plan.y] if plan else [],
                "method": plan.method if plan else "fallback",
                "screenshot": str(shot_path),
            },
        )

    return format_vision_tool_response(
        error_code=VisionToolErrorCode.OK,
        data={
            "screenshot": str(shot_path),
            "screenshot_after": str(after_path),
            "tap_x": plan.x if plan else 0,
            "tap_y": plan.y if plan else 0,
            "method": plan.method if plan else "spec_fallback",
            "hint_text": plan.hint_text if plan else "",
            "hint_gone": hint_gone,
            "exec_message": exec_msg[:200],
            "ocr_preview": ocr_summary[:800],
        },
    )
