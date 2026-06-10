"""登录后、隐私协议 checkbox 前的服务器选择连通性检查（严格弹窗判定）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from game_agent.services.adb_service import AdbService
from game_agent.services.server_selector_locator import (
    ENTER_POSITION_TOLERANCE_PX,
    find_enter_game_bbox,
)
from game_agent.utils.ocr_util import OcrBbox, extract_text_with_bbox

_LIST_TITLE_HINTS = re.compile(
    r"选择服务器|服务器列表|区服|选服|推荐服|切换服务器|server\s*list",
    re.IGNORECASE,
)

_STATIC_HINT_ONLY = re.compile(
    r"^click\s*to\s*select\s*server$|^select\s*server$|^点击选择",
    re.IGNORECASE,
)

_EXCLUDE_LIST_ROW = re.compile(
    r"login|password|协议|隐私|privacy|agree|copyright|publisher|版本|"
    r"sub-?account|踏入|开始游戏|进入游戏",
    re.IGNORECASE,
)

_DISMISS_HINTS = re.compile(
    r"^(关闭|关\s*闭|取消|返回|确定|OK|Close|Cancel|Back|×|X)$",
    re.IGNORECASE,
)

_EXIT_CONFIRM_NEGATIVE = re.compile(
    r"取消|返回游戏|否|暂不|继续游戏|留在此页|不退出|"
    r"cancel|stay|no|continue\s*game",
    re.IGNORECASE,
)

_EXIT_CONFIRM_POSITIVE = re.compile(
    r"退出游戏|退出|结束游戏|确认退出|是|yes|exit\s*game|quit",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ServerSelectorCheckResult:
    ok: bool
    message: str
    taps_used: int = 0
    panel_opened: bool = False


def _text_set(bboxes: list[OcrBbox]) -> set[str]:
    return {b.text.strip() for b in bboxes if b.text.strip()}


def is_page_navigation(
    before: list[OcrBbox],
    after: list[OcrBbox],
    enter_before: OcrBbox,
) -> bool:
    """整页跳转：进入按钮首次出现或位移过大。"""
    enter_after = find_enter_game_bbox(after)
    enter_was = find_enter_game_bbox(before)
    if enter_was is None and enter_after is not None:
        return True
    if enter_after is None:
        return True
    if abs(enter_after.cx - enter_before.cx) > ENTER_POSITION_TOLERANCE_PX:
        return True
    if abs(enter_after.cy - enter_before.cy) > ENTER_POSITION_TOLERANCE_PX:
        return True
    return False


def enter_still_same(enter_before: OcrBbox, after: list[OcrBbox]) -> bool:
    enter_after = find_enter_game_bbox(after)
    if enter_after is None:
        return False
    return (
        abs(enter_after.cx - enter_before.cx) <= ENTER_POSITION_TOLERANCE_PX
        and abs(enter_after.cy - enter_before.cy) <= ENTER_POSITION_TOLERANCE_PX
    )


def _has_dismiss(bboxes: list[OcrBbox]) -> bool:
    return any(_DISMISS_HINTS.search(b.text.strip()) for b in bboxes)


def _new_list_rows_above_enter(
    before: list[OcrBbox],
    after: list[OcrBbox],
    enter: OcrBbox,
) -> list[str]:
    before_set = _text_set(before)
    added: list[str] = []
    for bbox in after:
        text = bbox.text.strip()
        if not text or text in before_set:
            continue
        if bbox.cy >= enter.y1 or bbox.cy < enter.y1 - 420:
            continue
        if _EXCLUDE_LIST_ROW.search(text):
            continue
        if _STATIC_HINT_ONLY.search(text):
            continue
        added.append(text)
    return added


def server_list_panel_opened(
    before: list[OcrBbox],
    after: list[OcrBbox],
    enter_before: OcrBbox,
) -> bool:
    """同屏 overlay 弹窗证据；排除页面跳转误报。"""
    if is_page_navigation(before, after, enter_before):
        return False
    if not enter_still_same(enter_before, after):
        return False

    merged_after = " ".join(_text_set(after))
    if _LIST_TITLE_HINTS.search(merged_after):
        return True

    if _has_dismiss(after) and not _has_dismiss(before):
        return True

    new_rows = _new_list_rows_above_enter(before, after, enter_before)
    if len(new_rows) >= 2:
        return True

    return False


def find_dismiss_tap(bboxes: list[OcrBbox]) -> tuple[int, int] | None:
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = bbox.text.strip()
        if _DISMISS_HINTS.search(text):
            candidates.append((0 if text in ("关闭", "关 闭", "×", "X", "Cancel", "Close") else 1, bbox))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1].y1))
    best = candidates[0][1]
    return best.cx, best.cy


def find_exit_confirm_negative(bboxes: list[OcrBbox]) -> tuple[int, int] | None:
    negatives: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = bbox.text.strip()
        if _EXIT_CONFIRM_POSITIVE.search(text):
            continue
        if _EXIT_CONFIRM_NEGATIVE.search(text):
            negatives.append((len(text), bbox))
    if not negatives:
        return None
    negatives.sort(key=lambda item: (item[0], item[1].cy))
    best = negatives[0][1]
    return best.cx, best.cy


def has_exit_confirm_dialog(bboxes: list[OcrBbox]) -> bool:
    merged = " ".join(b.text for b in bboxes)
    if not re.search(r"退出|exit|quit", merged, re.IGNORECASE):
        return False
    return find_exit_confirm_negative(bboxes) is not None


def safe_outside_tap(width: int, height: int) -> tuple[int, int]:
    return int(width * 0.08), int(height * 0.12)


def _capture_ocr(
    adb: AdbService,
    artifact_root: Path,
    prefix: str,
) -> tuple[Path, list[OcrBbox]]:
    ts = datetime.now().strftime("%H%M%S_%f")
    shot = artifact_root / f"{prefix}_{ts}.png"
    adb.screencap_png(shot)
    sw, sh = adb.touch_size()
    bboxes = extract_text_with_bbox(shot, device_w=sw, device_h=sh)
    return shot, bboxes


def _try_close_panel(
    adb: AdbService,
    artifact_root: Path,
    width: int,
    height: int,
) -> list[str]:
    steps: list[str] = []
    _, bboxes = _capture_ocr(adb, artifact_root, "server_close")

    dismiss = find_dismiss_tap(bboxes)
    if dismiss is not None:
        dx, dy = dismiss
        adb.tap(dx, dy, width=width, height=height)
        steps.append(f"tap dismiss ({dx},{dy})")
        adb.wait_seconds(0.5)
    else:
        ox, oy = safe_outside_tap(width, height)
        adb.tap(ox, oy, width=width, height=height)
        steps.append(f"tap outside ({ox},{oy})")
        adb.wait_seconds(0.5)

    _, after_bboxes = _capture_ocr(adb, artifact_root, "server_after_dismiss")
    if has_exit_confirm_dialog(after_bboxes):
        neg = find_exit_confirm_negative(after_bboxes)
        if neg is not None:
            nx, ny = neg
            adb.tap(nx, ny, width=width, height=height)
            steps.append(f"tap exit-confirm negative ({nx},{ny})")
            adb.wait_seconds(0.4)

    return steps


def run_server_selector_check(
    adb: AdbService,
    artifact_root: Path,
    x: int,
    y: int,
    *,
    enter_bbox: OcrBbox,
    label: str = "",
    max_taps: int = 3,
) -> ServerSelectorCheckResult:
    """点击区服入口，验证同屏弹窗列表；3 次无弹窗 → FAILED。"""
    sw, sh = adb.touch_size()
    label_note = f" label={label!r}" if label else ""
    _, before_bboxes = _capture_ocr(adb, artifact_root, "server_before")

    for attempt in range(1, max_taps + 1):
        tap_msg = adb.tap(x, y, width=sw, height=sh)
        adb.wait_seconds(0.6)
        _, after_bboxes = _capture_ocr(adb, artifact_root, f"server_tap{attempt}")

        if server_list_panel_opened(before_bboxes, after_bboxes, enter_bbox):
            close_steps = _try_close_panel(adb, artifact_root, sw, sh)
            return ServerSelectorCheckResult(
                ok=True,
                message=(
                    f"[ServerCheck] PASSED attempt={attempt}{label_note} "
                    f"list panel opened (same screen). tap={tap_msg} "
                    f"close={' | '.join(close_steps)}"
                ),
                taps_used=attempt,
                panel_opened=True,
            )
        before_bboxes = after_bboxes

    return ServerSelectorCheckResult(
        ok=False,
        message=(
            f"[ServerCheck] FAILED after {max_taps} tap(s){label_note} — "
            "server list panel did not open on same screen. "
            "Use report_flow_done with [E2006]."
        ),
        taps_used=max_taps,
        panel_opened=False,
    )


# 兼容旧测试 import
server_panel_opened = server_list_panel_opened
