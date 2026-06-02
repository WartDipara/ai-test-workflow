from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunState:
    """跨轮次可变状态（由工具写入，Controller 读取）。"""

    finished: bool = False
    success: bool = False
    note: str = ""
    last_error: str | None = None
    round_hint: str = field(default="", repr=False)
    game_started: bool = False
    launch_wait_invoked: bool = False
