from __future__ import annotations

import asyncio
import threading
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

    def signal_fatal(self, reason: str) -> None:
        with self._lock:
            if not self._fatal_reason:
                self._fatal_reason = reason.strip()[:2000]
        self.stop_all.set()

    def get_fatal_reason(self) -> str | None:
        with self._lock:
            return self._fatal_reason

    def should_stop_executor(self) -> bool:
        return self.stop_all.is_set()

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

    def consume_reset_in_game_streak(self) -> bool:
        with self._lock:
            if not self._reset_in_game_streak:
                return False
            self._reset_in_game_streak = False
            return True
