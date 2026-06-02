from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ObserverSessionState:
    """观察者阶段共享会话状态（crash 重启后重置）。"""

    session_index: int = 1
    restarts_count: int = 0
    monitoring_enabled: bool = True

    entry_confirm_streak: int = 0
    entry_round_in_session: int = 0

    screen_stuck_count: int = 0
    screen_last_progress: str = ""
    screen_round_in_session: int = 0

    last_restart_reason: str = ""

    def reset_for_new_session(self, *, reason: str) -> None:
        self.session_index += 1
        self.restarts_count += 1
        self.last_restart_reason = reason[:2000]
        self.entry_confirm_streak = 0
        self.entry_round_in_session = 0
        self.screen_stuck_count = 0
        self.screen_last_progress = ""
        self.screen_round_in_session = 0

    def disable_monitoring(self) -> None:
        self.monitoring_enabled = False
