from game_agent.services.login_stage_probe import (
    login_stage_gate_message,
    probe_login_stage,
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


def test_sub_account_overlay_with_background_enter_game() -> None:
    """右侧小号面板 + 背景踏入仙途：仍应 blocking=sub_account_select。"""
    bboxes = [
        _bbox("踏入仙途", 1100, 770, 1300, 820),
        _bbox("I have read and agree", 900, 880, 1300, 920),
        _bbox("Sub-account description", 1700, 120, 2200, 160),
        _bbox("Sub-account1 (Last login)", 1500, 250, 2100, 300),
        _bbox("Create Sub-account", 1500, 900, 1900, 940),
        _bbox("Purchase Sub-account", 2000, 900, 2400, 940),
    ]
    probe = probe_login_stage(bboxes, screen_w=2400, screen_h=1080)
    assert probe.blocking is True
    assert probe.stage == "sub_account_select"
    assert probe.action_xy is not None
    assert "Sub-account1" in probe.action_label
    gate = login_stage_gate_message(bboxes, screen_w=2400, screen_h=1080)
    assert gate is not None
    assert "WRONG_STAGE" in gate
    assert "[E2006]" not in gate


def test_login_form_right_panel() -> None:
    bboxes = [
        _bbox("踏入仙途", 1100, 770, 1300, 820),
        _bbox("Log In", 1480, 170, 1580, 210),
        _bbox("Account, phone number, email", 1500, 290, 2100, 330),
        _bbox("Login Password", 1500, 430, 1900, 470),
    ]
    probe = probe_login_stage(bboxes, screen_w=2400, screen_h=1080)
    assert probe.blocking is True
    assert probe.stage == "login_form"


def test_clear_enter_game_screen() -> None:
    bboxes = [
        _bbox("踏入仙途", 1100, 770, 1300, 820),
        _bbox("Click to select Server", 1050, 620, 1400, 660),
    ]
    probe = probe_login_stage(bboxes, screen_w=2400, screen_h=1080)
    assert probe.blocking is False
    assert probe.stage == "clear"
    assert login_stage_gate_message(bboxes, screen_w=2400, screen_h=1080) is None


def test_create_purchase_only_no_tap_target() -> None:
    bboxes = [
        _bbox("Create Sub-account", 1500, 900, 1900, 940),
        _bbox("Purchase Sub-account", 2000, 900, 2400, 940),
        _bbox("Sub-account description", 1700, 120, 2200, 160),
    ]
    probe = probe_login_stage(bboxes, screen_w=2400, screen_h=1080)
    assert probe.blocking is True
    assert probe.stage == "sub_account_select"
    assert probe.action_xy is None
