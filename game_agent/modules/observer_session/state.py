from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ObserverSessionState:
    """观察者阶段共享会话状态（crash 重启后重置）。"""

    session_index: int = 1
    restarts_count: int = 0
    monitoring_enabled: bool = True
    last_restart_reason: str = ""

    def reset_for_new_session(self, *, reason: str) -> None:
        self.session_index += 1
        self.restarts_count += 1
        self.last_restart_reason = reason[:2000]

    def disable_monitoring(self) -> None:
        self.monitoring_enabled = False
