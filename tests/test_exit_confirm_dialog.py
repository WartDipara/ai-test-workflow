"""误触「退出游戏」确认框的 OCR 检测与恢复。"""

from __future__ import annotations

from unittest.mock import MagicMock

from game_agent.services.blocking_overlay import recover_exit_confirm_dialog_if_present
from game_agent.services.server_selector_check import (
    find_exit_confirm_negative,
    has_exit_confirm_dialog,
)
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, *, cx: int, cy: int) -> OcrBbox:
    return OcrBbox(text=text, x1=cx - 10, y1=cy - 10, x2=cx + 10, y2=cy + 10, cx=cx, cy=cy)


def test_has_exit_confirm_dialog_warm_tip() -> None:
    bboxes = [
        _bbox("温馨提示", cx=540, cy=800),
        _bbox("取消", cx=300, cy=1200),
        _bbox("退出游戏", cx=780, cy=1200),
    ]
    assert has_exit_confirm_dialog(bboxes) is True
    assert find_exit_confirm_negative(bboxes) == (300, 1200)


def test_recover_exit_confirm_taps_cancel() -> None:
    bboxes = [
        _bbox("退出游戏", cx=780, cy=1200),
        _bbox("取消", cx=300, cy=1200),
    ]
    adb = MagicMock()
    adb.tap.return_value = "tapped"
    msg = recover_exit_confirm_dialog_if_present(adb, bboxes, screen_w=1080, screen_h=2400)
    assert msg is not None
    adb.tap.assert_called_once_with(300, 1200, width=1080, height=2400)


def test_recover_exit_confirm_no_dialog() -> None:
    bboxes = [_bbox("开始游戏", cx=540, cy=2000)]
    adb = MagicMock()
    assert recover_exit_confirm_dialog_if_present(adb, bboxes, screen_w=1080, screen_h=2400) is None
    adb.tap.assert_not_called()
