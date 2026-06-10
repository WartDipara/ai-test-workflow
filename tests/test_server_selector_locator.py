from game_agent.services.server_selector_locator import (
    find_enter_game_bbox,
    locate_server_selector_target,
)
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, x1: int, y1: int, x2: int, y2: int) -> OcrBbox:
    return OcrBbox(
        text=text,
        cx=(x1 + x2) // 2,
        cy=(y1 + y2) // 2,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
    )


def test_16914_locate_above_enter_game() -> None:
    """回归：踏入仙途在下方，区服提示在上方。"""
    bboxes = [
        _bbox("踏入仙途", 1100, 770, 1300, 820),
        _bbox("Click to select Server", 1050, 620, 1400, 660),
        _bbox("Exclusive Sponsored Role Name", 1000, 600, 1350, 640),
        _bbox("I have read and agree", 900, 880, 1300, 920),
    ]
    enter = find_enter_game_bbox(bboxes)
    assert enter is not None
    assert "踏入" in enter.text
    target, enter2 = locate_server_selector_target(bboxes, screen_w=2400, screen_h=1080)
    assert enter2 is enter
    assert target is not None
    assert target.cy < enter.cy
    assert target.source == "ocr"


def test_sub_account_screen_no_enter_button() -> None:
    bboxes = [
        _bbox("Sub-account1 (Last login)", 1500, 250, 1900, 290),
        _bbox("Default", 1400, 180, 1500, 220),
        _bbox("Log In", 1480, 170, 1580, 210),
    ]
    assert find_enter_game_bbox(bboxes) is None
    target, enter = locate_server_selector_target(bboxes, screen_w=2400, screen_h=1080)
    assert enter is None
    assert target is None


def test_16914_skips_plus_noise_prefers_server_hint() -> None:
    """回归 140242：'+' 噪声不应压过 Click to select Server。"""
    bboxes = [
        _bbox("踏入仙途", 1100, 770, 1300, 820),
        _bbox("+", 1080, 655, 1100, 675),
        _bbox("----", 1050, 620, 1150, 660),
        _bbox("Click to select Server", 1160, 618, 1400, 658),
        _bbox("I have read and agree", 900, 880, 1300, 920),
    ]
    target, enter = locate_server_selector_target(bboxes, screen_w=2400, screen_h=1080)
    assert enter is not None
    assert target is not None
    assert target.label != "+"
    assert "select" in target.label.lower()


def test_fallback_when_no_ocr_in_band() -> None:
    bboxes = [_bbox("踏入仙途", 1100, 770, 1300, 820)]
    target, enter = locate_server_selector_target(bboxes, screen_w=2400, screen_h=1080)
    assert enter is not None
    assert target is not None
    assert target.source == "fallback"
    assert target.cy < enter.y1
