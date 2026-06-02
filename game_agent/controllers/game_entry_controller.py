from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from game_agent.models.settings import AppConfig
from game_agent.modules.observer_session.state import ObserverSessionState
from game_agent.services.adb_service import AdbService
from game_agent.services.game_launch import is_game_running
from game_agent.services.normal_exit import (
    NormalExitState,
    confirm_in_game_normal_exit,
)
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.utils.character_creation_ocr import match_character_creation_ocr
from game_agent.utils.ocr_util import extract_text_with_bounds
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GameEntryDetectResult:
    ok: bool
    message: str
    rounds: int = 0


@dataclass(slots=True)
class GameEntryDetector:
    """独立 AI 判定：是否已进入游戏内。"""

    adb: AdbService
    app_config: AppConfig
    artifact_root: Path
    exit_state: NormalExitState
    session_state: ObserverSessionState | None = None
    audit: RunAuditLogger | None = None

    async def run_until_normal_exit(
        self,
        stop_event: asyncio.Event,
    ) -> GameEntryDetectResult:
        """
        轮询截图 + 多模态判定；连续 N 次确认后调用 confirm_in_game_normal_exit。
        """
        cfg = self.app_config
        game_pkg = cfg.game.package_name
        llm_cfg = cfg.llm_multimodal or cfg.llm
        vision = VisionWorker(llm_cfg)

        timeout_s = cfg.game.main_screen_detect_timeout_s
        interval_s = cfg.game.main_screen_detect_poll_interval_s
        confirm_need = cfg.game.main_screen_confirm_rounds
        min_conf = cfg.game.main_screen_min_confidence

        logger.info(
            "[GameEntry] 开始等待进入游戏 | 包=%s | 超时=%.0fs | 间隔=%.1fs | 连续确认=%d",
            game_pkg,
            timeout_s,
            interval_s,
            confirm_need,
        )
        if self.audit is not None:
            self.audit.log_phase(
                "game_entry",
                "开始 AI 进入游戏判定",
                package=game_pkg,
                timeout_s=timeout_s,
                confirm_rounds=confirm_need,
            )

        deadline = time.monotonic() + timeout_s
        total_rounds = 0
        last_reason = ""
        session = self.session_state
        local_confirm_streak = 0

        while time.monotonic() < deadline and not stop_event.is_set():
            if session is not None and not session.monitoring_enabled:
                break

            total_rounds += 1
            if session is not None:
                session.entry_round_in_session += 1
                round_in_session = session.entry_round_in_session
                session_index = session.session_index
            else:
                round_in_session = total_rounds
                session_index = 1

            if not is_game_running(self.adb, game_pkg):
                if session is not None:
                    session.entry_confirm_streak = 0
                last_reason = f"游戏进程未运行 ({game_pkg})"
                logger.info(
                    "[GameEntry] s%02d 第 %d 轮 | %s",
                    session_index,
                    round_in_session,
                    last_reason,
                )
                await self._sleep_interval(interval_s, deadline, stop_event)
                continue

            shot_path = (
                self.artifact_root
                / f"entry_detect_s{session_index:02d}_{round_in_session:03d}.png"
            )
            try:
                self.adb.screencap_png(shot_path)
            except Exception as e:
                if session is not None:
                    session.entry_confirm_streak = 0
                last_reason = f"截图失败: {e}"
                logger.warning(
                    "[GameEntry] s%02d 第 %d 轮 %s",
                    session_index,
                    round_in_session,
                    last_reason,
                )
                await self._sleep_interval(interval_s, deadline, stop_event)
                continue

            try:
                ocr_summary = extract_text_with_bounds(shot_path)
            except Exception as e:
                ocr_summary = f"[OCR failed] {e}"

            ocr_creation_hits = match_character_creation_ocr(ocr_summary)
            restarts = session.restarts_count if session is not None else 0
            judgment = await vision.judge_in_game_main(
                screenshot_path=shot_path,
                ocr_summary=ocr_summary,
                ocr_creation_hits=ocr_creation_hits,
                round_id=round_in_session,
                session_index=session_index,
                sessions_restarted=restarts,
            )

            if self.audit is not None:
                self.audit.log_observer(
                    kind="game_entry_judgment",
                    message=judgment.reason[:500],
                    round_id=total_rounds,
                    extra={
                        **judgment.model_dump(),
                        "session_index": session_index,
                        "round_in_session": round_in_session,
                    },
                )

            if ocr_creation_hits:
                if session is not None:
                    session.entry_confirm_streak = 0
                last_reason = (
                    f"OCR 命中创角关键词 {ocr_creation_hits}，仍判为未进入游戏"
                )
                logger.info(
                    "[GameEntry] s%02d 第 %d 轮 | %s",
                    session_index,
                    round_in_session,
                    last_reason,
                )
                await self._sleep_interval(interval_s, deadline, stop_event)
                continue

            if (
                judgment.in_game_main
                and judgment.confidence >= min_conf
                and "character_creation" not in judgment.blockers
            ):
                if session is not None:
                    session.entry_confirm_streak += 1
                    confirm_streak = session.entry_confirm_streak
                else:
                    local_confirm_streak += 1
                    confirm_streak = local_confirm_streak
                last_reason = judgment.reason
                logger.info(
                    "[GameEntry] s%02d 第 %d 轮 | 确认 %d/%d | conf=%.2f | stage=%s",
                    session_index,
                    round_in_session,
                    confirm_streak,
                    confirm_need,
                    judgment.confidence,
                    judgment.stage,
                )
                if confirm_streak >= confirm_need:
                    exit_result = await confirm_in_game_normal_exit(
                        adb=self.adb,
                        cfg=cfg,
                        state=self.exit_state,
                        session_state=session,
                        audit=self.audit,
                        summary=judgment.reason[:2000],
                    )
                    if self.audit is not None:
                        self.audit.log_phase(
                            "game_entry",
                            "AI 判定已进入游戏并完成正常退出",
                            rounds=total_rounds,
                            session_index=session_index,
                        )
                    return GameEntryDetectResult(
                        ok=True,
                        message=exit_result.message,
                        rounds=total_rounds,
                    )
            else:
                if session is not None:
                    session.entry_confirm_streak = 0
                else:
                    local_confirm_streak = 0
                last_reason = judgment.reason or f"stage={judgment.stage}"
                logger.info(
                    "[GameEntry] s%02d 第 %d 轮 | 未满足 | in_game=%s conf=%.2f",
                    session_index,
                    round_in_session,
                    judgment.in_game_main,
                    judgment.confidence,
                )

            await self._sleep_interval(interval_s, deadline, stop_event)

        fail = (
            f"在 {timeout_s:.0f}s 内 AI 未确认进入游戏 ({game_pkg})。"
            f"最后说明: {last_reason}"
        )
        logger.warning("[GameEntry] %s", fail)
        if self.audit is not None:
            self.audit.log_phase("game_entry", "进入游戏判定超时", rounds=total_rounds)
        return GameEntryDetectResult(ok=False, message=fail, rounds=total_rounds)

    @staticmethod
    async def _sleep_interval(
        interval_s: float,
        deadline: float,
        stop_event: asyncio.Event,
    ) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or stop_event.is_set():
            return
        await asyncio.sleep(min(interval_s, max(0.1, remaining)))
