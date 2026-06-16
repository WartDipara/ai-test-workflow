from __future__ import annotations

from game_agent.services.login_form_ocr import resolve_login_form_targets
from game_agent.utils.ocr_util import OcrLine


def _line(text: str, x: int, y: int) -> str:
  return f"- ({x}, {y}) '{text}' (0.95)"


def test_resolve_login_password_placeholder_login_password() -> None:
    body = "\n".join(
        [
            _line("Log In", 540, 400),
            _line("Ariakagami39@gmail.com", 540, 520),
            _line("Login password", 540, 600),
            _line("Forgot password", 800, 650),
        ]
    )
    targets = resolve_login_form_targets(body, screen_height=2400)
    assert targets.password_xy == (540, 600)
    assert targets.account_xy == (540, 520)
