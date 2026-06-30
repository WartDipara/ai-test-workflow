from __future__ import annotations

from game_agent.services.login_stage_probe import (
    detect_split_screen_login,
    probe_login_stage,
)
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, cx: int, cy: int = 400) -> OcrBbox:
    return OcrBbox(
        text=text,
        cx=cx,
        cy=cy,
        x1=cx - 40,
        y1=cy - 12,
        x2=cx + 40,
        y2=cy + 12,
    )


def test_split_screen_login_detected() -> None:
    screen_w = 2800
    bboxes = [
        _bbox("開始遊戲", cx=500),
        _bbox("登录", cx=2200),
        _bbox("忘记密码", cx=2250),
        _bbox("账号", cx=2180),
    ]
    assert detect_split_screen_login(bboxes, screen_w=screen_w) is True
    probe = probe_login_stage(bboxes, screen_w=screen_w, screen_h=1260)
    assert probe.blocking is True
    assert probe.stage == "login_form"
    assert "split_screen_login" in probe.reason


def test_portrait_login_only_right_panel() -> None:
    screen_w = 1080
    bboxes = [
        _bbox("登录", cx=800),
        _bbox("忘记密码", cx=820),
        _bbox("账号", cx=780),
    ]
    assert detect_split_screen_login(bboxes, screen_w=screen_w) is False
    probe = probe_login_stage(bboxes, screen_w=screen_w, screen_h=2400)
    assert probe.blocking is True
    assert probe.stage == "login_form"
    assert "split_screen_login" not in probe.reason
