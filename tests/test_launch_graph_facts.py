from __future__ import annotations

from game_agent.graphs.launch_facts import classify_screen_facts
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


def test_classify_detects_login_and_enter_cta() -> None:
    bboxes = [
        _bbox("账号", 900, 500, 1000, 540),
        _bbox("密码", 900, 580, 1000, 620),
        _bbox("登录", 900, 700, 1000, 740),
        _bbox("开始游戏", 400, 850, 600, 900),
    ]
    facts = classify_screen_facts(bboxes, screen_w=1080, screen_h=2400)
    assert facts.login_blocking is True
    assert facts.enter_cta_visible is True


def test_classify_terms_checkbox_on_agreement_line() -> None:
    bboxes = [
        _bbox("我已详细阅读并同意用户协议和隐私保护指引", 110, 793, 385, 811),
        _bbox("开始游戏", 400, 850, 600, 900),
    ]
    facts = classify_screen_facts(bboxes, screen_w=435, screen_h=955)
    assert facts.terms_checkbox_visible is True
    assert facts.enter_cta_visible is True


def test_classify_cold_start_privacy_modal_not_checkbox() -> None:
    """全屏隐私弹窗：点底部「同意」，勿走协议行 checkbox 左偏移。"""
    bboxes = [
        _bbox("用户协议和隐私政策", 200, 600, 880, 660),
        _bbox(
            "我们非常重视您的个人信息和隐私保护。为了更好地保障您的个人权益，"
            "在使用我们的产品前，请您认真阅读【用户协议】和【隐私政策】的全部内容。",
            120,
            700,
            960,
            900,
        ),
        _bbox("权限使用目的说明", 200, 950, 600, 990),
        _bbox("读取唯一设备标识符，保障账号使用安全", 250, 1080, 900, 1120),
        _bbox("不同意", 150, 1250, 450, 1320),
        _bbox("同意", 630, 1250, 930, 1320),
    ]
    facts = classify_screen_facts(bboxes, screen_w=1080, screen_h=2400)
    assert facts.initial_privacy_dialog is True
    assert facts.agree_button_xy == (780, 1285)
    assert facts.terms_checkbox_visible is False
    assert facts.login_blocking is False


def test_classify_detects_notice_as_announcement_overlay() -> None:
    bboxes = [
        _bbox("Notice", 400, 400, 680, 450),
        _bbox("日常通知", 200, 1780, 340, 1820),
        _bbox("Start Game", 480, 1960, 600, 2000),
    ]
    facts = classify_screen_facts(
        bboxes,
        screen_w=1080,
        screen_h=2400,
        ocr_summary="Notice 日常通知 Start Game",
    )
    assert facts.announcement_overlay is True
    assert facts.enter_cta_visible is True


def test_classify_17690_server_slot_ocr_not_fallback() -> None:
    """17690 竖屏：OCR 区服行应令 server_slot_visible=True（非 fallback）。"""
    bboxes = [
        _bbox("1 ServerXQ删档内测1服", 312, 1644, 744, 1695),
        _bbox("Agree? User Agreement and Privacy Policy", 199, 1746, 936, 1792),
        _bbox("Start Game", 409, 1953, 669, 2005),
    ]
    facts = classify_screen_facts(bboxes, screen_w=1080, screen_h=2400)
    assert facts.server_slot_visible is True
    assert facts.enter_cta_visible is True
