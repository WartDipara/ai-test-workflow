from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field


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
    deploy_package_verified: bool = False
    session_restarts: int = 0
    session_index: int = 1
    _in_game_confirmed: bool = field(default=False, repr=False)
    _in_game_note: str = field(default="", repr=False)
    _in_game_agent_deadline: float = field(default=0.0, repr=False)
    _in_game_play_deadline: float = field(default=0.0, repr=False)
    _in_game_play_buffer_s: float = field(default=60.0, repr=False)
    _ocr_busy: bool = field(default=False, repr=False)

    def signal_fatal(self, reason: str) -> None:
        with self._lock:
            if not self._fatal_reason:
                self._fatal_reason = reason.strip()[:2000]
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
            if (note or "").strip():
                self._in_game_note = note.strip()[:2000]

    def signal_in_game_agent_phase(self, run_s: float) -> None:
        """稳定性观察通过后启动 in-game play，延长并行阶段 deadline（兼容旧接口）。"""
        with self._lock:
            deadline = time.monotonic() + max(60.0, float(run_s))
            self._in_game_agent_deadline = deadline
            self._in_game_play_deadline = deadline
            self.ui_stage = "in_game_play"

    def signal_in_game_play_started(self, play_deadline: float, buffer_s: float = 60.0) -> None:
        """进入游戏内试玩：orchestrator 以 play_deadline + buffer 延长并行阶段。"""
        with self._lock:
            self._in_game_play_deadline = float(play_deadline)
            self._in_game_play_buffer_s = max(0.0, float(buffer_s))
            self._in_game_agent_deadline = self._in_game_play_deadline + self._in_game_play_buffer_s
            self.ui_stage = "in_game_play"

    def extend_parallel_deadline(self, base_deadline: float) -> float:
        with self._lock:
            if self._in_game_play_deadline > 0:
                return max(
                    base_deadline,
                    self._in_game_play_deadline + self._in_game_play_buffer_s,
                )
            if self._in_game_agent_deadline > 0:
                return max(base_deadline, self._in_game_agent_deadline)
            return base_deadline

    def is_in_game_play_active(self) -> bool:
        with self._lock:
            if self._in_game_confirmed:
                return False
            if self._in_game_play_deadline <= 0:
                return False
            return time.monotonic() < self._in_game_play_deadline

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
