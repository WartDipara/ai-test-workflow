"""登录/登录后过渡阶段安全键盘黑屏：检测与逐级收起。"""

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

POST_LOGIN_BLACK_SCREENCAP_HINT = (
    "登录已提交但截屏几乎全黑、OCR 无文字，通常是安全键盘仍挡住画面（例如已切到小號页但焦点未释放）。"
    "先等待 1～2 轮；连续黑屏第 3 轮按返回键尝试收起键盘。"
)

_BLACKOUT_DISMISS_STREAK = 3
_BLACKOUT_EXTRA_WAIT_S = 2.5


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


def should_handle_secure_keyboard_blackout(state: LaunchGraphState) -> bool:
    """登录中或登录后过渡（未选小号/未进游戏）时，黑屏应按安全键盘处理。"""
    if state.get("in_game_confirmed") or state.get("in_game_entry_passed"):
        return False
    if is_login_flow_in_progress(state):
        return True
    if state.get("login_done") and not state.get("sub_account_selected"):
        return True
    return False


def is_secure_keyboard_blackout(
    screenshot_path: Path | str,
    bboxes: list[OcrBbox],
    *,
    ocr_summary: str = "",
) -> bool:
    """截屏几乎全黑且 OCR 无有效文字（安全键盘常见）。"""
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


def is_login_secure_keyboard_blackout(
    screenshot_path: Path | str,
    bboxes: list[OcrBbox],
    *,
    ocr_summary: str = "",
) -> bool:
    """兼容旧名。"""
    return is_secure_keyboard_blackout(
        screenshot_path,
        bboxes,
        ocr_summary=ocr_summary,
    )


def blackout_streak(state: LaunchGraphState) -> int:
    return int(state.get("secure_keyboard_blackout_streak") or 0)


def bump_blackout_streak(state: LaunchGraphState) -> int:
    streak = blackout_streak(state) + 1
    state["secure_keyboard_blackout_streak"] = streak
    return streak


def reset_blackout_streak(state: LaunchGraphState) -> None:
    state["secure_keyboard_blackout_streak"] = 0


def should_press_back_for_blackout(streak: int) -> bool:
    return streak >= _BLACKOUT_DISMISS_STREAK


def blackout_hint_for_state(state: LaunchGraphState) -> str:
    if state.get("login_done"):
        return POST_LOGIN_BLACK_SCREENCAP_HINT
    return LOGIN_BLACK_SCREENCAP_HINT


def try_dismiss_secure_keyboard(
    adb: AdbService,
    executor: ExecutorSection,
    *,
    force_press_back: bool = False,
) -> str:
    """点击空白区收起键盘；force_press_back 时必定再按 BACK。"""
    sw, sh = adb.touch_size()
    return dismiss_secure_keyboard_focus(
        adb.device_serial,
        width=sw,
        height=sh,
        settle_s=min(executor.credential_fill_settle_s, 0.4),
        press_back=force_press_back or executor.dismiss_keyboard_press_back,
    )


def try_dismiss_login_secure_keyboard(
    adb: AdbService,
    executor: ExecutorSection,
) -> str:
    """兼容旧名。"""
    return try_dismiss_secure_keyboard(adb, executor)

