from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from game_agent.models.settings import ExecutorSection
from game_agent.services.login_batch_fill import (
    atomic_login_fill_and_submit,
    fill_login_at_coords,
)
from game_agent.services.login_form_ocr import LoginFormOcrTargets


def _executor(**kwargs) -> ExecutorSection:
    defaults = {
        "login_submit_press_enter": True,
        "use_cached_login_button_xy": True,
        "dismiss_keyboard_after_password": True,
        "dismiss_keyboard_press_back": False,
        "credential_fill_settle_s": 0.25,
    }
    defaults.update(kwargs)
    return ExecutorSection(**defaults)


def test_fill_login_at_coords_skips_enter_submit_by_default() -> None:
    with patch(
        "game_agent.services.login_batch_fill.fill_login_with_enter_flow",
        return_value=(True, "account via pick | password via focused"),
    ) as mock_fill:
        result = fill_login_at_coords(
            "serial1",
            account_xy=(100, 200),
            password_xy=None,
            username="user@example.com",
            password="secret",
            executor=_executor(),
            screen_width=2400,
            screen_height=1080,
        )
    assert result.ok is True
    mock_fill.assert_called_once()
    assert mock_fill.call_args.kwargs["submit_via_enter"] is False


def test_fill_login_at_coords_can_opt_in_enter_submit() -> None:
    with patch(
        "game_agent.services.login_batch_fill.fill_login_with_enter_flow",
        return_value=(True, "ok"),
    ) as mock_fill:
        fill_login_at_coords(
            "serial1",
            account_xy=(100, 200),
            password_xy=None,
            username="u",
            password="p",
            executor=_executor(),
            screen_width=2400,
            screen_height=1080,
            submit_via_enter=True,
        )
    assert mock_fill.call_args.kwargs["submit_via_enter"] is True


def test_atomic_login_calls_submit_with_cached_login_button(tmp_path: Path) -> None:
    targets = LoginFormOcrTargets(
        account_xy=(1518, 201),
        password_xy=(1518, 350),
        login_button_xy=(1800, 520),
        account_text="account",
        password_text="Password",
        login_text="sign in",
    )
    adb = MagicMock()
    adb.device_serial = "dev1"
    adb.wait_seconds = MagicMock()

    ocr_result = MagicMock()
    ocr_result.targets = targets
    ocr_result.ocr_summary = "[Login form OCR] account=(1518,201)"

    verify_result = MagicMock()
    verify_result.left_login_form = True
    verify_result.stage = "clear"
    verify_result.message = "stage=clear"
    verify_result.ocr_summary = "welcome"
    verify_result.screenshot = tmp_path / "verify.png"

    with (
        patch(
            "game_agent.services.login_batch_fill.ocr_login_field_coords",
            return_value=ocr_result,
        ),
        patch(
            "game_agent.services.login_batch_fill.fill_login_at_coords",
            return_value=MagicMock(ok=True, message="account via pick | password via focused"),
        ) as mock_fill,
        patch(
            "game_agent.services.login_batch_fill.submit_login_after_password",
            return_value="[1] u2 ENTER | [3] cached Login tap (1800,520)",
        ) as mock_submit,
        patch(
            "game_agent.services.login_batch_fill.try_dismiss_login_secure_keyboard",
            return_value="Dismiss tap (2328,32)",
        ),
        patch(
            "game_agent.services.login_batch_fill.verify_login_with_ocr",
            return_value=verify_result,
        ),
    ):
        result = atomic_login_fill_and_submit(
            adb,
            username="user@example.com",
            password="secret",
            executor=_executor(),
            artifact_root=tmp_path,
            screen_width=2400,
            screen_height=1080,
            cached_login_xy=(1700, 500),
        )

    assert result.ok is True
    assert result.left_login_form is True
    mock_fill.assert_called_once()
    assert mock_fill.call_args.kwargs["submit_via_enter"] is False
    mock_submit.assert_called_once()
    submit_kwargs = mock_submit.call_args.kwargs
    assert submit_kwargs["cached_login_xy"] == (1800, 520)
    assert submit_kwargs["password_y"] == 350
    assert submit_kwargs["press_enter"] is True
    assert submit_kwargs["use_cached_coords"] is True
    assert "cached Login tap" in result.message


def test_atomic_login_submit_uses_run_state_cache_when_ocr_missing_button(
    tmp_path: Path,
) -> None:
    targets = LoginFormOcrTargets(
        account_xy=(100, 200),
        login_button_xy=None,
    )
    adb = MagicMock()
    adb.device_serial = "dev1"
    adb.wait_seconds = MagicMock()

    ocr_result = MagicMock()
    ocr_result.targets = targets
    ocr_result.ocr_summary = "ocr"

    verify_result = MagicMock()
    verify_result.left_login_form = False
    verify_result.stage = "login_form"
    verify_result.message = "still login"
    verify_result.ocr_summary = "account"
    verify_result.screenshot = tmp_path / "verify.png"

    with (
        patch(
            "game_agent.services.login_batch_fill.ocr_login_field_coords",
            return_value=ocr_result,
        ),
        patch(
            "game_agent.services.login_batch_fill.fill_login_at_coords",
            return_value=MagicMock(ok=True, message="filled"),
        ),
        patch(
            "game_agent.services.login_batch_fill.submit_login_after_password",
            return_value="[3] cached Login tap (900,600)",
        ) as mock_submit,
        patch(
            "game_agent.services.login_batch_fill.try_dismiss_login_secure_keyboard",
            return_value="dismissed",
        ),
        patch(
            "game_agent.services.login_batch_fill.verify_login_with_ocr",
            return_value=verify_result,
        ),
    ):
        atomic_login_fill_and_submit(
            adb,
            username="u",
            password="p",
            executor=_executor(use_cached_login_button_xy=True),
            artifact_root=tmp_path,
            screen_width=2400,
            screen_height=1080,
            cached_login_xy=(900, 600),
        )

    assert mock_submit.call_args.kwargs["cached_login_xy"] == (900, 600)


def test_atomic_login_stops_on_fill_failure(tmp_path: Path) -> None:
    targets = LoginFormOcrTargets(account_xy=(100, 200))
    adb = MagicMock()
    adb.device_serial = "dev1"

    ocr_result = MagicMock()
    ocr_result.targets = targets
    ocr_result.ocr_summary = "ocr"

    with (
        patch(
            "game_agent.services.login_batch_fill.ocr_login_field_coords",
            return_value=ocr_result,
        ),
        patch(
            "game_agent.services.login_batch_fill.fill_login_at_coords",
            return_value=MagicMock(ok=False, message="password field not found"),
        ),
        patch(
            "game_agent.services.login_batch_fill.submit_login_after_password",
        ) as mock_submit,
    ):
        result = atomic_login_fill_and_submit(
            adb,
            username="u",
            password="p",
            executor=_executor(),
            artifact_root=tmp_path,
            screen_width=2400,
            screen_height=1080,
        )

    assert result.ok is False
    mock_submit.assert_not_called()


def test_fill_login_with_enter_flow_submit_via_enter_flag() -> None:
    """accessibility_input: submit_via_enter=False 时不应出现 ENTER submit 步骤。"""
    device = MagicMock()
    account_el = MagicMock()
    pwd_el = MagicMock()
    device.click = MagicMock()

    with (
        patch(
            "game_agent.services.accessibility_input._connect_u2",
            return_value=device,
        ),
        patch(
            "game_agent.services.accessibility_input._enumerate_edits",
            return_value=[(account_el, 10, 20, None)],
        ),
        patch(
            "game_agent.services.accessibility_input._pick_credential_edit",
            side_effect=[
                (account_el, "pick", 10, 20),
                (pwd_el, "pwd-pick", 10, 80),
            ],
        ),
        patch(
            "game_agent.services.accessibility_input._focused_editable",
            side_effect=[None, pwd_el],
        ),
        patch("game_agent.services.accessibility_input.fill_edit_text_u2"),
        patch(
            "game_agent.services.accessibility_input.press_enter_key",
            return_value="u2 ENTER",
        ) as mock_enter,
    ):
        from game_agent.services.accessibility_input import fill_login_with_enter_flow

        ok, msg = fill_login_with_enter_flow(
            "serial",
            account_xy=(10, 20),
            username="user",
            password="pass",
            width=2400,
            height=1080,
            submit_via_enter=False,
        )

    assert ok is True
    assert "ENTER next-field" in msg
    assert "ENTER submit" not in msg
    mock_enter.assert_called_once()

    mock_enter.reset_mock()
    with (
        patch(
            "game_agent.services.accessibility_input._connect_u2",
            return_value=device,
        ),
        patch(
            "game_agent.services.accessibility_input._enumerate_edits",
            return_value=[(account_el, 10, 20, None)],
        ),
        patch(
            "game_agent.services.accessibility_input._pick_credential_edit",
            side_effect=[
                (account_el, "pick", 10, 20),
                (pwd_el, "pwd-pick", 10, 80),
            ],
        ),
        patch(
            "game_agent.services.accessibility_input._focused_editable",
            side_effect=[None, pwd_el],
        ),
        patch("game_agent.services.accessibility_input.fill_edit_text_u2"),
        patch(
            "game_agent.services.accessibility_input.press_enter_key",
            return_value="u2 ENTER",
        ) as mock_enter2,
    ):
        from game_agent.services.accessibility_input import fill_login_with_enter_flow

        ok2, msg2 = fill_login_with_enter_flow(
            "serial",
            account_xy=(10, 20),
            username="user",
            password="pass",
            width=2400,
            height=1080,
            submit_via_enter=True,
        )

    assert ok2 is True
    assert "ENTER submit" in msg2
    assert mock_enter2.call_count == 2
