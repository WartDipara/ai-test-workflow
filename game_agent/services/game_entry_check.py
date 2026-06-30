from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from game_agent.models.game_entry_judgment import GameEntryJudgment
from game_agent.models.run_state import RunState
from game_agent.models.settings import AppConfig
from game_agent.models.vision_tool_result import VisionToolErrorCode, format_vision_tool_response
from game_agent.services.adb_service import AdbService
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.utils.character_creation_ocr import match_character_creation_ocr
from game_agent.utils.ocr_util import extract_text_with_bounds
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InGameCheckResult:
    judgment: GameEntryJudgment | None
    ocr_creation_hits: list[str]
    screenshot_path: Path | None
    streak: int
    confirm_needed: int
    confirmed: bool
    """JSON 字符串（errorCode + data），供主脑工具回调解析。"""
    message: str


def _finalize_confirmed_run_state(
    run_state: RunState,
    *,
    game_pkg: str,
    judgment: GameEntryJudgment,
    streak: int,
) -> None:
    run_state.in_game_confirmed = True
    run_state.game_started = True
    run_state.finished = True
    run_state.success = True
    run_state.note = (judgment.reason or "In-game confirmed")[:2000]
    logger.info(
        "[check_in_game] Confirmed in-game for %s | streak=%d | conf=%.2f",
        game_pkg,
        streak,
        judgment.confidence,
    )


async def run_in_game_check_on_capture(
    *,
    shot_path: Path,
    ocr_summary: str,
    cfg: AppConfig,
    run_state: RunState,
    audit: RunAuditLogger | None = None,
    round_id: int = 0,
    sessions_restarted: int = 0,
    session_index: int = 1,
    confirm_needed: int | None = None,
    provisional: bool = False,
    attempt_context=None,
) -> InGameCheckResult:
    """对已有截图+OCR 做多模态进游戏判定；provisional 时不写 run_state 终局字段。"""
    game_pkg = cfg.game.package_name
    confirm_need = confirm_needed if confirm_needed is not None else cfg.game.main_screen_confirm_rounds
    min_conf = cfg.game.main_screen_min_confidence
    llm_cfg = cfg.llm_multimodal
    if llm_cfg is None:
        body = format_vision_tool_response(
            error_code=VisionToolErrorCode.NO_MULTIMODAL,
            error_message="llm_multimodal not configured (check_in_game)",
        )
        return InGameCheckResult(
            judgment=None,
            ocr_creation_hits=[],
            screenshot_path=shot_path,
            streak=run_state.in_game_confirm_streak,
            confirm_needed=confirm_need,
            confirmed=False,
            message=body,
        )

    ocr_creation_hits = match_character_creation_ocr(ocr_summary)
    from game_agent.modules.session_invalidation import capture_session_generation, discard_if_stale

    work_gen = capture_session_generation(attempt_context)
    vision = VisionWorker(llm_cfg, attempt_context=attempt_context)
    try:
        judgment = await vision.judge_in_game_main(
            screenshot_path=shot_path,
            ocr_summary=ocr_summary,
            ocr_creation_hits=ocr_creation_hits,
            round_id=round_id,
            session_index=max(1, session_index),
            sessions_restarted=sessions_restarted,
        )
    except Exception as e:
        logger.exception("check_in_game multimodal API failed")
        body = format_vision_tool_response(
            error_code=VisionToolErrorCode.API_ERROR,
            error_message=str(e)[:800],
            data={"screenshot": str(shot_path), "ocr_preview": ocr_summary[:500]},
        )
        return InGameCheckResult(
            judgment=None,
            ocr_creation_hits=ocr_creation_hits,
            screenshot_path=shot_path,
            streak=0,
            confirm_needed=confirm_need,
            confirmed=False,
            message=body,
        )
    if discard_if_stale(work_gen, where="check_in_game", ctx=attempt_context):
        body = format_vision_tool_response(
            error_code=VisionToolErrorCode.API_ERROR,
            error_message="stale_session_discard",
        )
        return InGameCheckResult(
            judgment=None,
            ocr_creation_hits=ocr_creation_hits,
            screenshot_path=shot_path,
            streak=run_state.in_game_confirm_streak,
            confirm_needed=confirm_need,
            confirmed=False,
            message=body,
        )

    if audit is not None:
        audit.log_observer(
            kind="check_in_game",
            message=judgment.reason,
            round_id=round_id,
            extra=judgment.model_dump(),
        )

    if ocr_creation_hits:
        run_state.in_game_confirm_streak = 0
        body = format_vision_tool_response(
            error_code=VisionToolErrorCode.OK,
            data={
                "in_game_main": False,
                "confirmed": False,
                "streak": 0,
                "confirm_needed": confirm_need,
                "stage": judgment.stage,
                "confidence": judgment.confidence,
                "ocr_creation_hits": ocr_creation_hits,
                "reason": judgment.reason,
                "screenshot": str(shot_path),
                "hint": "Creation/login OCR hit; continue login flow, do not treat as in-game.",
            },
        )
        return InGameCheckResult(
            judgment=judgment,
            ocr_creation_hits=ocr_creation_hits,
            screenshot_path=shot_path,
            streak=0,
            confirm_needed=confirm_need,
            confirmed=False,
            message=body,
        )

    ok_sample = (
        judgment.in_game_main
        and judgment.confidence >= min_conf
        and "character_creation" not in judgment.blockers
    )
    if ok_sample:
        run_state.in_game_confirm_streak += 1
    else:
        run_state.in_game_confirm_streak = 0

    streak = run_state.in_game_confirm_streak
    confirmed = streak >= confirm_need
    if confirmed and not provisional:
        _finalize_confirmed_run_state(
            run_state,
            game_pkg=game_pkg,
            judgment=judgment,
            streak=streak,
        )
    elif confirmed and provisional:
        logger.info(
            "[check_in_game] Provisional in-game entry for %s | streak=%d | conf=%.2f",
            game_pkg,
            streak,
            judgment.confidence,
        )

    body = format_vision_tool_response(
        error_code=VisionToolErrorCode.OK,
        data={
            "in_game_main": judgment.in_game_main,
            "confidence": judgment.confidence,
            "stage": judgment.stage,
            "blockers": judgment.blockers,
            "ocr_signals": judgment.ocr_signals,
            "reason": judgment.reason,
            "streak": streak,
            "confirm_needed": confirm_need,
            "confirmed": confirmed,
            "provisional": provisional,
            "screenshot": str(shot_path),
            "hint": (
                "CONFIRMED — proceed to stability observe."
                if confirmed and provisional
                else (
                    "CONFIRMED — stop tapping and end tool use."
                    if confirmed
                    else f"Need {confirm_need - streak} more positive check_in_game sample(s)."
                )
            ),
        },
    )
    return InGameCheckResult(
        judgment=judgment,
        ocr_creation_hits=ocr_creation_hits,
        screenshot_path=shot_path,
        streak=streak,
        confirm_needed=confirm_need,
        confirmed=confirmed,
        message=body,
    )


async def run_in_game_check(
    *,
    adb: AdbService,
    cfg: AppConfig,
    run_state: RunState,
    artifact_root: Path,
    audit: RunAuditLogger | None = None,
    round_id: int = 0,
    sessions_restarted: int = 0,
    session_index: int = 1,
    provisional: bool = False,
    attempt_context=None,
) -> InGameCheckResult:
    """主脑工具：截图 + OCR + 多模态进游戏判定；更新 confirm streak。"""
    confirm_need = cfg.game.main_screen_confirm_rounds
    llm_cfg = cfg.llm_multimodal
    if llm_cfg is None:
        body = format_vision_tool_response(
            error_code=VisionToolErrorCode.NO_MULTIMODAL,
            error_message="llm_multimodal not configured (check_in_game)",
        )
        return InGameCheckResult(
            judgment=None,
            ocr_creation_hits=[],
            screenshot_path=None,
            streak=run_state.in_game_confirm_streak,
            confirm_needed=confirm_need,
            confirmed=False,
            message=body,
        )

    shot_path = artifact_root / f"check_in_game_{round_id:03d}.png"
    try:
        adb.screencap_png(shot_path)
    except Exception as e:
        body = format_vision_tool_response(
            error_code=VisionToolErrorCode.API_ERROR,
            error_message=f"screencap failed: {e}",
        )
        return InGameCheckResult(
            judgment=None,
            ocr_creation_hits=[],
            screenshot_path=None,
            streak=run_state.in_game_confirm_streak,
            confirm_needed=confirm_need,
            confirmed=False,
            message=body,
        )

    try:
        dw, dh = adb.touch_size()
        ocr_summary = extract_text_with_bounds(shot_path, device_w=dw, device_h=dh)
    except Exception as e:
        ocr_summary = f"[OCR failed] {e}"

    return await run_in_game_check_on_capture(
        shot_path=shot_path,
        ocr_summary=ocr_summary,
        cfg=cfg,
        run_state=run_state,
        audit=audit,
        round_id=round_id,
        sessions_restarted=sessions_restarted,
        session_index=session_index,
        provisional=provisional,
        attempt_context=attempt_context,
    )
