"""登录阶段安全键盘黑屏：检测与收起焦点。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from game_agent.services.accessibility_input import dismiss_secure_keyboard_focus
from game_agent.utils.ocr_util import OcrBbox, is_screencap_mostly_black

if TYPE_CHECKING:
    from game_agent.models.launch_graph_state import LaunchGraphState
    from game_agent.models.settings import ExecutorSection
    from game_agent.services.adb_service import AdbService

LOGIN_BLACK_SCREENCAP_HINT = (
    "登录阶段正常应有账号/密码/登录按钮等 UI；若截屏几乎全黑、OCR 无任何文字，"
    "通常是密码框仍持有焦点、系统安全键盘遮住整屏。应点击空白区收起键盘后再 OCR/提交。"
)


def is_login_flow_in_progress(state: LaunchGraphState) -> bool:
    """凭状态位判断是否在登录子树流程中（不依赖当轮 OCR）。"""
    if state.get("login_done"):
        return False
    if state.get("login_submitted"):
        return False
    stage = str(state.get("current_stage") or "")
    if stage == "login_form":
        return True
    if state.get("account_filled") or state.get("password_filled"):
        return True
    last_route = str(state.get("last_route") or "")
    return last_route in ("atomic_login",)


def is_login_secure_keyboard_blackout(
    screenshot_path: Path | str,
    bboxes: list[OcrBbox],
    *,
    ocr_summary: str = "",
) -> bool:
    """登录阶段黑屏：图像几乎全黑且 OCR 无有效文字。"""
    if not is_screencap_mostly_black(screenshot_path):
        return False
    if bboxes:
        non_empty = [b for b in bboxes if (b.text or "").strip()]
        if len(non_empty) >= 2:
            return False
    summary = (ocr_summary or "").strip()
    if summary and len(summary) > 40:
        return False
    return True


def try_dismiss_login_secure_keyboard(
    adb: AdbService,
    executor: ExecutorSection,
) -> str:
    """点击空白区（可选 BACK）退出密码框焦点。"""
    sw, sh = adb.touch_size()
    return dismiss_secure_keyboard_focus(
        adb.device_serial,
        width=sw,
        height=sh,
        settle_s=min(executor.credential_fill_settle_s, 0.4),
        press_back=executor.dismiss_keyboard_press_back,
    )
