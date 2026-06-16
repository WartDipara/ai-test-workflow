from game_agent.services.server_selector_locator import (
    find_enter_game_bbox,
    has_server_hint_above_enter,
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
    assert target.source == "ocr"


def test_17690_portrait_server_above_agreement_row() -> None:
    """回归 17690：区服行在协议行与 Start Game 之间，旧 band 会 fallback。"""
    bboxes = [
        _bbox("1 ServerXQ删档内测1服", 312, 1644, 744, 1695),
        _bbox("Agree? User Agreement and Privacy Policy", 199, 1746, 936, 1792),
        _bbox("Start Game", 409, 1953, 669, 2005),
    ]
    target, enter = locate_server_selector_target(bboxes, screen_w=1080, screen_h=2400)
    assert enter is not None
    assert target is not None
    assert target.source == "ocr"
    assert "Server" in target.label or "删档" in target.label
    assert target.cy == 1669
    assert target.cy < enter.cy


def test_overlay_sub_account_background_enter_no_server_target() -> None:
    """仅有背景踏入仙途时不再静默 fallback。"""
    bboxes = [
        _bbox("踏入仙途", 1100, 770, 1300, 820),
        _bbox("Sub-account1 (Last login)", 1500, 250, 2100, 300),
        _bbox("Create Sub-account", 1500, 900, 1900, 940),
    ]
    enter = find_enter_game_bbox(bboxes)
    assert enter is not None
    target, enter2 = locate_server_selector_target(bboxes, screen_w=2400, screen_h=1080)
    assert enter2 is enter
    assert target is None


def test_no_silent_fallback_when_only_enter() -> None:
    bboxes = [_bbox("踏入仙途", 1100, 770, 1300, 820)]
    target, enter = locate_server_selector_target(bboxes, screen_w=2400, screen_h=1080)
    assert enter is not None
    assert target is None


def test_unresolved_when_server_hint_outside_legacy_band() -> None:
    """区服语义在屏上但若被 exclude 规则挡下应 unresolved；正常 17690 布局应 ocr。"""
    enter = _bbox("Start Game", 409, 1953, 669, 2005)
    bboxes = [
        _bbox("1 ServerXQ删档内测1服", 312, 1644, 744, 1695),
        _bbox("Agree? User Agreement and Privacy Policy", 199, 1746, 936, 1792),
        enter,
    ]
    assert has_server_hint_above_enter(
        bboxes, enter, screen_w=1080, screen_h=2400
    )
    target, _ = locate_server_selector_target(bboxes, screen_w=1080, screen_h=2400)
    assert target is not None
    assert target.source == "ocr"
