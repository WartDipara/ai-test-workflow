from __future__ import annotations

from game_agent.services.login_form_ocr import (
    is_compound_login_label,
    is_standalone_login_label,
    resolve_login_form_targets,
)


def test_standalone_sign_in_is_login_button() -> None:
    assert is_standalone_login_label("sign in") is True
    assert is_standalone_login_label("Sign in") is True
    assert is_standalone_login_label("SIGN IN") is True
    assert is_compound_login_label("sign in") is False


def test_sign_in_with_google_is_compound() -> None:
    assert is_compound_login_label("Sign in With Google") is True
    assert is_standalone_login_label("Sign in With Google") is False


def test_resolve_login_form_picks_sign_in_button() -> None:
    ocr_body = """\
- (1518, 201) account
- (1518, 350) Password
- (1800, 520) sign in
- (1900, 700) Sign in With Google
"""
    targets = resolve_login_form_targets(ocr_body, screen_height=1080)
    assert targets.login_button_xy == (1800, 520)
    assert targets.login_text.lower() == "sign in"


def test_resolve_login_form_still_picks_login() -> None:
    ocr_body = "- (1200, 500) Login\n- (1200, 300) Password\n"
    targets = resolve_login_form_targets(ocr_body, screen_height=1080)
    assert targets.login_button_xy == (1200, 500)
