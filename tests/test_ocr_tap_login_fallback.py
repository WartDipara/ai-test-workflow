from __future__ import annotations

from unittest.mock import MagicMock, patch

from game_agent.services.accessibility_input import fill_login_with_ocr_tap_fallback


def test_ocr_tap_fallback_failure_returns_three_tuple_not_crash() -> None:
    """失败路径须返回三元组，避免 ValueError。"""
    adb = MagicMock()
    adb.tap.return_value = "Tapped"
    device = MagicMock()

    with (
        patch(
            "game_agent.services.accessibility_input._connect_u2",
            return_value=device,
        ),
        patch(
            "game_agent.services.accessibility_input._enumerate_edits",
            return_value=[],
        ),
        patch(
            "game_agent.services.accessibility_input._focused_editable",
            return_value=None,
        ),
        patch(
            "game_agent.services.accessibility_input._pick_credential_edit",
            return_value=(None, "none", 0, 0),
        ),
        patch(
            "game_agent.services.accessibility_input._attempt_ime_send_keys",
            return_value=(False, "ime failed"),
        ),
        patch(
            "game_agent.services.accessibility_input._attempt_light_paste",
            return_value=(False, "paste failed"),
        ),
    ):
        ok, msg = fill_login_with_ocr_tap_fallback(
            adb,
            serial="dev1",
            account_xy=(1699, 379),
            password_xy=(1734, 554),
            username="user@example.com",
            password="secret12",
            width=2800,
            height=1260,
        )

    assert ok is False
    assert "fill_path=ocr-hybrid" in msg


def test_ocr_tap_fallback_webview_uses_ime_when_no_edits() -> None:
    adb = MagicMock()
    device = MagicMock()

    with (
        patch(
            "game_agent.services.accessibility_input._connect_u2",
            return_value=device,
        ),
        patch(
            "game_agent.services.accessibility_input._enumerate_edits",
            return_value=[],
        ),
        patch(
            "game_agent.services.accessibility_input._focused_editable",
            return_value=None,
        ),
        patch(
            "game_agent.services.accessibility_input._pick_credential_edit",
            return_value=(None, "none", 0, 0),
        ),
        patch(
            "game_agent.services.accessibility_input._attempt_ime_send_keys",
            return_value=(True, "Tapped ime-send_keys"),
        ),
        patch(
            "game_agent.services.accessibility_input._attempt_light_paste",
            return_value=(False, "skip"),
        ),
    ):
        ok, msg = fill_login_with_ocr_tap_fallback(
            adb,
            serial="dev1",
            account_xy=(1699, 379),
            password_xy=(1734, 554),
            username="user@example.com",
            password="secret12",
            width=2800,
            height=1260,
        )

    assert ok is True
    assert "ime-send_keys" in msg


def test_ocr_tap_fallback_uses_u2_set_text_when_focused() -> None:
    adb = MagicMock()
    adb.tap.return_value = "Tapped"
    device = MagicMock()
    focused = MagicMock()
    focused.info = {"text": "user@example.com"}

    with (
        patch(
            "game_agent.services.accessibility_input._connect_u2",
            return_value=device,
        ),
        patch(
            "game_agent.services.accessibility_input._enumerate_edits",
            return_value=[(focused, 1849, 342, {})],
        ),
        patch(
            "game_agent.services.accessibility_input._focused_editable",
            side_effect=[focused, MagicMock(info={"text": "******"})],
        ),
        patch(
            "game_agent.services.accessibility_input._pick_credential_edit",
            return_value=(None, "none", 0, 0),
        ),
        patch("game_agent.services.accessibility_input.fill_edit_text_u2"),
        patch(
            "game_agent.services.accessibility_input._attempt_ime_send_keys",
            return_value=(False, "skip"),
        ),
        patch(
            "game_agent.services.accessibility_input._attempt_light_paste",
            return_value=(False, "skip"),
        ),
        patch(
            "game_agent.services.accessibility_input._read_editable_text",
            side_effect=["user@example.com", "secret12"],
        ),
        patch(
            "game_agent.services.accessibility_input._node_center_distance",
            return_value=(1850, 360, 5.0),
        ),
    ):
        ok, msg = fill_login_with_ocr_tap_fallback(
            adb,
            serial="dev1",
            account_xy=(1849, 342),
            password_xy=(1732, 647),
            username="user@example.com",
            password="secret12",
            width=2800,
            height=1260,
        )

    assert ok is True
    assert "u2-focused" in msg or "u2-" in msg
