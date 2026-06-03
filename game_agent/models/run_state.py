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
    in_game_confirmed: bool = False
    in_game_confirm_streak: int = 0
    package_install_confirmed: bool = False
    failure_code: str = ""
    last_declared_stage: str = ""
    # 键盘弹出前 get_ocr_summary 缓存的主 Login 坐标（安全键盘下截屏常黑屏）
    cached_login_button_xy: tuple[int, int] | None = None
    cached_login_button_text: str = ""
