from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AttemptContext:
    """
    Shared state for parallel executor + monitors within one retry attempt.
    Async monitors write; executor thread reads between rounds.
    """

    stop_all: asyncio.Event = field(default_factory=asyncio.Event)
    attempt_index: int = 1
    max_attempts: int = 1
    prior_attempt_brief: str = ""
    _fatal_reason: str | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    ui_stage: str = ""
    ui_progress: str = ""
    _reset_in_game_streak: bool = field(default=False, repr=False)
    _session_relogin_recovery: bool = field(default=False, repr=False)
    _session_generation: int = field(default=0, repr=False)
    _session_invalidate_event: threading.Event = field(default_factory=threading.Event, repr=False)
    deploy_package_verified: bool = False
    session_restarts: int = 0
    session_index: int = 1
    _in_game_confirmed: bool = field(default=False, repr=False)
    _in_game_note: str = field(default="", repr=False)
    _in_game_play_session_active: bool = field(default=False, repr=False)
    _ocr_busy: bool = field(default=False, repr=False)
    _foreground_lost: bool = field(default=False, repr=False)
    _foreground_cv: threading.Condition = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_foreground_cv", threading.Condition(self._lock))

    def signal_fatal(self, reason: str) -> None:
        with self._lock:
            if not self._fatal_reason:
                self._fatal_reason = reason.strip()[:2000]
            self._foreground_cv.notify_all()
        self.stop_all.set()

    def get_fatal_reason(self) -> str | None:
        with self._lock:
            return self._fatal_reason

    def should_stop_executor(self) -> bool:
        if self.stop_all.is_set():
            return True
        try:
            from game_agent.services.shutdown import is_shutdown_requested

            return is_shutdown_requested()
        except Exception:
            return False

    def set_session_restarts(self, count: int) -> None:
        with self._lock:
            self.session_restarts = max(0, int(count))

    def set_session_index(self, index: int) -> None:
        with self._lock:
            self.session_index = max(1, int(index))

    def get_session_index(self) -> int:
        with self._lock:
            return self.session_index

    def set_ui_observation(self, stage: str, progress: str = "") -> None:
        with self._lock:
            self.ui_stage = (stage or "unknown").strip()[:64]
            self.ui_progress = (progress or "").strip()[:64]

    def get_ui_observation(self) -> tuple[str, str]:
        with self._lock:
            return self.ui_stage or "", self.ui_progress or ""

    def format_observer_hint(self) -> str:
        stage, progress = self.get_ui_observation()
        stage = stage or "unknown"
        if progress:
            return f"Observer UI hint: stage={stage} progress={progress}"
        return f"Observer UI hint: stage={stage}"

    def request_reset_in_game_streak(self) -> None:
        with self._lock:
            self._reset_in_game_streak = True

    def request_session_relogin_recovery(self) -> None:
        with self._lock:
            self._session_relogin_recovery = True
            self._reset_in_game_streak = True

    def bump_session_generation(self, reason: str = "") -> int:
        """进程消失/重启：递增世代号并通知 executor 丢弃进行中的 API 结果。"""
        with self._lock:
            self._session_generation += 1
            gen = self._session_generation
        self._session_invalidate_event.set()
        logger.warning(
            "[AttemptContext] session generation → %d | %s",
            gen,
            (reason or "session_invalidate")[:200],
        )
        return gen

    def get_session_generation(self) -> int:
        with self._lock:
            return self._session_generation

    def is_session_generation_stale(self, captured: int) -> bool:
        if captured <= 0:
            return self._session_invalidate_event.is_set()
        with self._lock:
            return captured < self._session_generation

    def is_session_invalidated(self) -> bool:
        return self._session_invalidate_event.is_set()

    def acknowledge_session_invalidation(self) -> None:
        """observe/classify 消费重启信号后可清除通知位（世代号不回退）。"""
        self._session_invalidate_event.clear()

    def consume_session_relogin_recovery(self) -> bool:
        with self._lock:
            if not self._session_relogin_recovery:
                return False
            self._session_relogin_recovery = False
            return True

    def mark_deploy_package_verified(self) -> None:
        with self._lock:
            self.deploy_package_verified = True

    def consume_deploy_package_verified(self) -> bool:
        with self._lock:
            if not self.deploy_package_verified:
                return False
            self.deploy_package_verified = False
            return True

    def consume_reset_in_game_streak(self) -> bool:
        with self._lock:
            if not self._reset_in_game_streak:
                return False
            self._reset_in_game_streak = False
            return True

    def signal_in_game_confirmed(self, note: str = "") -> None:
        """Executor 在 in-game play 完成且无异常后通知 orchestrator。"""
        with self._lock:
            self._in_game_confirmed = True
            self._in_game_play_session_active = False
            if (note or "").strip():
                self._in_game_note = note.strip()[:2000]

    def signal_in_game_play_started(self) -> None:
        """进入游戏内试玩：orchestrator 等待 executor 终态。"""
        with self._lock:
            self._in_game_play_session_active = True
            self.ui_stage = "in_game_play"

    def clear_in_game_play_session(self) -> None:
        with self._lock:
            self._in_game_play_session_active = False

    def is_in_game_play_active(self) -> bool:
        with self._lock:
            if self._in_game_confirmed:
                return False
            return self._in_game_play_session_active

    def is_in_game_confirmed(self) -> bool:
        with self._lock:
            return self._in_game_confirmed

    def get_in_game_note(self) -> str:
        with self._lock:
            return self._in_game_note

    def set_ocr_busy(self, busy: bool) -> None:
        with self._lock:
            self._ocr_busy = bool(busy)

    def is_ocr_busy(self) -> bool:
        with self._lock:
            return self._ocr_busy

    def set_foreground_lost(self, lost: bool) -> None:
        with self._foreground_cv:
            self._foreground_lost = bool(lost)
            if not lost:
                self._foreground_cv.notify_all()

    def is_foreground_lost(self) -> bool:
        with self._lock:
            return self._foreground_lost

    def wait_foreground_ready(self, timeout: float | None = None) -> bool:
        """阻塞直到前台恢复或超时。返回 True 表示可继续执行。"""
        with self._foreground_cv:
            if not self._foreground_lost:
                return True
            if timeout is None:
                while self._foreground_lost:
                    self._foreground_cv.wait()
                return True
            deadline = time.monotonic() + max(0.0, timeout)
            while self._foreground_lost:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return not self._foreground_lost
                self._foreground_cv.wait(timeout=remaining)
            return True


def block_until_foreground_ready(
    attempt_context: AttemptContext | None,
    *,
    poll_interval_s: float = 10.0,
) -> bool:
    """
    前台失焦时阻塞 executor；返回 False 表示应中止（fatal / shutdown）。
    """
    if attempt_context is None:
        return True
    if not attempt_context.is_foreground_lost():
        return True
    wait_s = max(2.0, float(poll_interval_s) * 2)
    while attempt_context.is_foreground_lost():
        if attempt_context.should_stop_executor():
            return False
        attempt_context.wait_foreground_ready(timeout=wait_s)
    return not attempt_context.should_stop_executor()
