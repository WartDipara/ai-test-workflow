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
    # atomic_login OCR 阶段缓存的登录按钮坐标（安全键盘黑屏时仍可点击）
    cached_login_button_xy: tuple[int, int] | None = None
    cached_login_button_text: str = ""
    # (line_x1, cy, base_offset_px, char_width_px) — step>0 时复用，避免重复 OCR
    checkbox_bbox_cache: tuple[int, int, int, int] | None = None
    # launch 流程阶段追踪（显式状态，不依赖 prompt 记忆）
    launch_stage: str = "launch"
    server_checked: bool = False
    server_check_attempts: int = 0
    privacy_checkbox_tapped: bool = False
    last_stage_error: str = ""
    graph_state_snapshot: dict = field(default_factory=dict)

    def format_launch_stage_status(self) -> str:
        return (
            f"[LaunchStage] stage={self.launch_stage!r} "
            f"server_checked={self.server_checked} "
            f"privacy_checkbox_tapped={self.privacy_checkbox_tapped} "
            f"server_check_attempts={self.server_check_attempts}"
            + (f" last_error={self.last_stage_error!r}" if self.last_stage_error else "")
        )
