from game_agent.models.server_connectivity_probe import ServerConnectivityProbe
from game_agent.services.server_selector_check import ServerSelectorCheckResult
from game_agent.services.server_selector_pipeline import (
    _ocr_indicates_empty_slot,
    finalize_tap_check_result,
    message_indicates_e2006,
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


def _empty_probe() -> ServerConnectivityProbe:
    return ServerConnectivityProbe(
        on_enter_game_screen=True,
        enter_button_visible=True,
        server_slot_status="empty",
        recommendation="tap_verify",
        confidence=0.95,
        reason="dashes visible",
    )


def _failed_tap() -> ServerSelectorCheckResult:
    return ServerSelectorCheckResult(
        ok=False,
        message="[ServerCheck] FAILED after 3 tap(s) — panel did not open.",
        taps_used=3,
        panel_opened=False,
    )


def test_message_indicates_e2006() -> None:
    assert message_indicates_e2006("[ServerCheck] FAILED [E2006] — empty")
    assert not message_indicates_e2006("[ServerCheck] WRONG_STAGE")


def test_empty_plus_tap_fail_upgrades_to_e2006() -> None:
    probe_msg = (
        "[ServerProbe] on_enter_game_screen=True enter_button_visible=True "
        "server_slot_status=empty has_network_error_ui=False "
        "recommendation=tap_verify conf=0.95 reason='empty'\n"
    )
    result = finalize_tap_check_result(
        probe_msg=probe_msg,
        probe=_empty_probe(),
        tap_result=_failed_tap(),
        slot_empty=True,
    )
    assert result.ok is False
    assert "[E2006]" in result.message
    assert "empty server slot" in result.message
    assert result.taps_used == 3


def test_tap_fail_without_empty_slot_keeps_original_message() -> None:
    probe_msg = "[ServerProbe] server_slot_status=ready\n"
    ready_probe = ServerConnectivityProbe(
        on_enter_game_screen=True,
        enter_button_visible=True,
        server_slot_status="ready",
        recommendation="tap_verify",
    )
    result = finalize_tap_check_result(
        probe_msg=probe_msg,
        probe=ready_probe,
        tap_result=_failed_tap(),
        slot_empty=False,
    )
    assert "[E2006]" not in result.message
    assert "FAILED after 3 tap(s)" in result.message


def test_16914_dash_in_band_counts_as_empty_slot() -> None:
    enter = _bbox("踏入仙途", 1100, 770, 1300, 820)
    bboxes = [
        enter,
        _bbox("----", 1050, 620, 1150, 660),
        _bbox("Click to select Server", 1160, 620, 1400, 660),
    ]
    assert _ocr_indicates_empty_slot(bboxes, enter, screen_w=2400, screen_h=1080) is True


def test_tap_success_unchanged() -> None:
    probe_msg = "[ServerProbe] empty\n"
    passed = ServerSelectorCheckResult(
        ok=True,
        message="[ServerCheck] PASSED attempt=1",
        taps_used=1,
        panel_opened=True,
    )
    result = finalize_tap_check_result(
        probe_msg=probe_msg,
        probe=_empty_probe(),
        tap_result=passed,
        slot_empty=True,
    )
    assert result.ok is True
    assert "PASSED" in result.message
