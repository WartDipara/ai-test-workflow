"""atomic_login_fill_and_submit 调用顺序单测。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from game_agent.services.login_batch_fill import (
    AtomicLoginResult,
    FillLoginAtCoordsResult,
    OcrLoginCoordsResult,
    VerifyLoginOcrResult,
    atomic_login_fill_and_submit,
)
from game_agent.services.login_form_ocr import LoginFormOcrTargets


def test_atomic_login_calls_ocr_fill_submit_verify_in_order(tmp_path: Path) -> None:
    adb = MagicMock()
    adb.device_serial = "dev1"
    executor = MagicMock()
    executor.credential_fill_settle_s = 0.4
    executor.login_submit_press_enter = True
    executor.use_cached_login_button_xy = True
    executor.dismiss_keyboard_after_password = True
    executor.dismiss_keyboard_press_back = False
    executor.credential_fill_max_distance_px = 150.0

    targets = LoginFormOcrTargets(
        account_xy=(100, 200),
        password_xy=(100, 300),
        login_button_xy=(200, 400),
    )
    calls: list[str] = []

    def _ocr(*_a, **_k):
        calls.append("ocr")
        return OcrLoginCoordsResult(
            targets=targets,
            ocr_summary="ocr body",
            screenshot=tmp_path / "ocr.png",
        )

    def _fill(*_a, **_k):
        calls.append("fill")
        return FillLoginAtCoordsResult(ok=True, message="filled")

    def _submit(*_a, **_k):
        calls.append("submit")
        return True, "enter ok"

    def _verify(*_a, **_k):
        calls.append("verify")
        return VerifyLoginOcrResult(
            left_login_form=True,
            stage="clear",
            message="left login",
            ocr_summary="after",
            screenshot=tmp_path / "verify.png",
        )

    with (
        patch("game_agent.services.login_batch_fill.ocr_login_field_coords", side_effect=_ocr),
        patch("game_agent.services.login_batch_fill.fill_login_at_coords", side_effect=_fill),
        patch("game_agent.services.login_batch_fill.submit_login_after_fill", side_effect=_submit),
        patch("game_agent.services.login_batch_fill.verify_login_with_ocr", side_effect=_verify),
    ):
        result = atomic_login_fill_and_submit(
            adb,
            username="user@example.com",
            password="secret",
            executor=executor,
            artifact_root=tmp_path,
            screen_width=1080,
            screen_height=2400,
        )

    assert calls == ["ocr", "fill", "submit", "verify"]
    assert result.ok is True
    assert result.left_login_form is True


def test_atomic_login_stops_on_fill_failure(tmp_path: Path) -> None:
    adb = MagicMock()
    adb.device_serial = "dev1"
    executor = MagicMock()
    executor.credential_fill_max_distance_px = 150.0

    targets = LoginFormOcrTargets(account_xy=(1, 2), password_xy=(3, 4))

    with (
        patch(
            "game_agent.services.login_batch_fill.ocr_login_field_coords",
            return_value=OcrLoginCoordsResult(
                targets=targets,
                ocr_summary="",
                screenshot=tmp_path / "o.png",
            ),
        ),
        patch(
            "game_agent.services.login_batch_fill.fill_login_at_coords",
            return_value=FillLoginAtCoordsResult(ok=False, message="fill failed"),
        ),
        patch("game_agent.services.login_batch_fill.submit_login_after_fill") as submit_mock,
        patch("game_agent.services.login_batch_fill.verify_login_with_ocr") as verify_mock,
    ):
        result = atomic_login_fill_and_submit(
            adb,
            username="u",
            password="p",
            executor=executor,
            artifact_root=tmp_path,
            screen_width=1080,
            screen_height=2400,
        )

    submit_mock.assert_not_called()
    verify_mock.assert_not_called()
    assert result.ok is False
