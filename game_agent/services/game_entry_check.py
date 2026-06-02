from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from game_agent.models.game_entry_judgment import GameEntryJudgment
from game_agent.models.run_state import RunState
from game_agent.models.settings import AppConfig
from game_agent.services.adb_service import AdbService
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.utils.character_creation_ocr import match_character_creation_ocr
from game_agent.utils.ocr_util import extract_text_with_bounds
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InGameCheckResult:
    judgment: GameEntryJudgment
    ocr_creation_hits: list[str]
    screenshot_path: Path
    streak: int
    confirm_needed: int
    confirmed: bool
    message: str


async def run_in_game_check(
    *,
    adb: AdbService,
    cfg: AppConfig,
    run_state: RunState,
    artifact_root: Path,
    audit: RunAuditLogger | None = None,
    round_id: int = 0,
    sessions_restarted: int = 0,
) -> InGameCheckResult:
    """Screenshot + multimodal in-game judgment; updates run_state confirm streak."""
    game_pkg = cfg.game.package_name
    confirm_need = cfg.game.main_screen_confirm_rounds
    min_conf = cfg.game.main_screen_min_confidence
    llm_cfg = cfg.llm_multimodal or cfg.llm
    vision = VisionWorker(llm_cfg)

    shot_path = artifact_root / f"check_in_game_{round_id:03d}.png"
    adb.screencap_png(shot_path)

    try:
        ocr_summary = extract_text_with_bounds(shot_path)
    except Exception as e:
        ocr_summary = f"[OCR failed] {e}"

    ocr_creation_hits = match_character_creation_ocr(ocr_summary)
    judgment = await vision.judge_in_game_main(
        screenshot_path=shot_path,
        ocr_summary=ocr_summary,
        ocr_creation_hits=ocr_creation_hits,
        round_id=round_id,
        session_index=1,
        sessions_restarted=sessions_restarted,
    )

    if audit is not None:
        audit.log_observer(
            kind="check_in_game",
            message=judgment.reason[:500],
            round_id=round_id,
            extra=judgment.model_dump(),
        )

    if ocr_creation_hits:
        run_state.in_game_confirm_streak = 0
        msg = (
            f"Not in game: OCR creation keywords {ocr_creation_hits}. "
            f"in_game={judgment.in_game_main} conf={judgment.confidence:.2f} "
            f"stage={judgment.stage} reason={judgment.reason[:300]}"
        )
        return InGameCheckResult(
            judgment=judgment,
            ocr_creation_hits=ocr_creation_hits,
            screenshot_path=shot_path,
            streak=0,
            confirm_needed=confirm_need,
            confirmed=False,
            message=msg,
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
    if confirmed:
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

    msg = (
        f"in_game_main={judgment.in_game_main} conf={judgment.confidence:.2f} "
        f"stage={judgment.stage} streak={streak}/{confirm_need}"
        + (" — CONFIRMED, stop tapping." if confirmed else "")
        + (f" reason={judgment.reason[:400]}" if judgment.reason else "")
    )
    return InGameCheckResult(
        judgment=judgment,
        ocr_creation_hits=ocr_creation_hits,
        screenshot_path=shot_path,
        streak=streak,
        confirm_needed=confirm_need,
        confirmed=confirmed,
        message=msg,
    )
