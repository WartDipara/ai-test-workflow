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
    # (line_x1, cy, half_char_px, _) — step>0 时复用，避免重复 OCR
    checkbox_bbox_cache: tuple[int, int, int, int] | None = None
    # launch 流程阶段追踪（显式状态，不依赖 prompt 记忆）
    launch_stage: str = "launch"
    server_checked: bool = False
    server_check_attempts: int = 0
    last_stage_error: str = ""

    def format_launch_stage_status(self) -> str:
        return (
            f"[LaunchStage] stage={self.launch_stage!r} "
            f"server_checked={self.server_checked} "
            f"server_check_attempts={self.server_check_attempts}"
            + (f" last_error={self.last_stage_error!r}" if self.last_stage_error else "")
        )
