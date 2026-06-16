from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from game_agent.services.checkbox_locator import locate_privacy_checkbox
from game_agent.services.privacy_checkbox import (
    ensure_privacy_checkbox_checked,
    message_indicates_list_panel_failed,
    screen_has_privacy_terms,
)
from game_agent.services.server_selector_check import ServerSelectorCheckResult
from game_agent.services.server_selector_pipeline import (
    run_full_server_selector_check_with_privacy_precheck,
)
from game_agent.utils.ocr_util import OcrBbox
from tests.checkbox_images import (
    CHECKBOX_AFTER_CHECKED,
    CHECKBOX_AFTER_UNCHECKED,
    CHECKBOX_BEFORE,
    ROI_CHANGE_THRESHOLD,
    SCREEN_H,
    SCREEN_W,
    checkbox_roi_from_before,
    copy_checkbox_screencap,
    require_checkbox_images,
    roi_diff_vs_before,
)


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


def test_screen_has_privacy_terms_english() -> None:
    bboxes = [_bbox("I have read and agree", 900, 880, 1300, 920)]
    assert screen_has_privacy_terms(bboxes, 2400, 1080) is True


def test_screen_has_privacy_terms_chinese() -> None:
    bboxes = [_bbox("已阅读并同意隐私政策", 800, 900, 1200, 940)]
    assert screen_has_privacy_terms(bboxes, 2400, 1080) is True


def test_message_indicates_list_panel_failed() -> None:
    assert message_indicates_list_panel_failed(
        "[ServerCheck] FAILED after 3 tap(s) — server list panel did not open on same screen."
    )
    assert message_indicates_list_panel_failed(
        "[ServerCheck] FAILED [E2006] — empty server slot and list panel did not open after tap verification."
    )
    assert not message_indicates_list_panel_failed("[ServerCheck] WRONG_STAGE")


def test_ensure_skipped_when_already_tapped(tmp_path: Path) -> None:
    adb = MagicMock()
    result = ensure_privacy_checkbox_checked(
        adb,
        tmp_path,
        already_tapped=True,
    )
    assert result.action == "skipped"
    assert result.tapped is False
    assert result.verified is True
    adb.screencap_png.assert_not_called()


@pytest.fixture(scope="module", autouse=True)
def _checkbox_fixture_images() -> None:
    require_checkbox_images()


def test_roi_mean_abs_diff_detects_checked_on_fixture() -> None:
    diff_checked = roi_diff_vs_before(CHECKBOX_AFTER_CHECKED)
    diff_unchecked = roi_diff_vs_before(CHECKBOX_AFTER_UNCHECKED)
    assert diff_checked >= ROI_CHANGE_THRESHOLD
    assert diff_unchecked < ROI_CHANGE_THRESHOLD
    assert diff_checked > diff_unchecked


def test_roi_picks_checked_candidate_among_after_images() -> None:
    candidates = {
        CHECKBOX_AFTER_CHECKED.name: roi_diff_vs_before(CHECKBOX_AFTER_CHECKED),
        CHECKBOX_AFTER_UNCHECKED.name: roi_diff_vs_before(CHECKBOX_AFTER_UNCHECKED),
    }
    winner = max(candidates, key=candidates.get)
    assert winner == CHECKBOX_AFTER_CHECKED.name
    assert candidates[winner] >= ROI_CHANGE_THRESHOLD


def test_checkbox_roi_from_before_locates_on_fixture() -> None:
    located, box = checkbox_roi_from_before()
    assert located.cx > 0 and located.cy > 0
    assert box[2] > box[0] and box[3] > box[1]
    _, box_again = checkbox_roi_from_before()
    assert box == box_again


def test_ensure_verified_when_roi_changes(tmp_path: Path) -> None:
    adb = MagicMock()
    adb.touch_size.return_value = (SCREEN_W, SCREEN_H)
    adb.tap.return_value = "Tapped (850,900)"

    screencap_calls = {"n": 0}

    def fake_screencap(path: Path) -> None:
        screencap_calls["n"] += 1
        source = CHECKBOX_BEFORE if screencap_calls["n"] == 1 else CHECKBOX_AFTER_CHECKED
        copy_checkbox_screencap(path, source)

    adb.screencap_png.side_effect = fake_screencap

    result = ensure_privacy_checkbox_checked(adb, tmp_path, prefix="test_cb", step=0)
    assert result.action == "tapped"
    assert result.verified is True
    assert result.roi_diff >= ROI_CHANGE_THRESHOLD
    adb.tap.assert_called_once()
    adb.wait_seconds.assert_called_once_with(0.4)


def test_ensure_failed_when_roi_unchanged(tmp_path: Path) -> None:
    adb = MagicMock()
    adb.touch_size.return_value = (SCREEN_W, SCREEN_H)
    adb.tap.return_value = "Tapped"

    screencap_calls = {"n": 0}

    def fake_screencap(path: Path) -> None:
        screencap_calls["n"] += 1
        source = CHECKBOX_BEFORE if screencap_calls["n"] == 1 else CHECKBOX_AFTER_UNCHECKED
        copy_checkbox_screencap(path, source)

    adb.screencap_png.side_effect = fake_screencap

    result = ensure_privacy_checkbox_checked(adb, tmp_path, prefix="test_cb", max_steps=1)
    assert result.action == "failed"
    assert result.verified is False
    assert result.roi_diff < ROI_CHANGE_THRESHOLD
    assert "did not change checkbox ROI" in result.message


def test_precheck_wrapper_taps_then_runs_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from game_agent.services import server_selector_pipeline as pipe_mod

    ensure_calls: list[bool] = []

    async def fake_ensure(_adb, _root, **kwargs):
        ensure_calls.append(kwargs.get("already_tapped", False))
        from game_agent.services.privacy_checkbox import PrivacyCheckboxEnsureResult

        if kwargs.get("prefix") == "privacy_cb_precheck":
            return PrivacyCheckboxEnsureResult(
                action="tapped",
                message="[PrivacyCheckbox] VERIFIED",
                tapped=True,
                verified=True,
            )
        return PrivacyCheckboxEnsureResult(action="skipped", message="skip", tapped=False)

    async def fake_server_check(*_args, **_kwargs):
        return ServerSelectorCheckResult(
            ok=True,
            message="[ServerCheck] PASSED",
            taps_used=1,
            panel_opened=True,
        )

    monkeypatch.setattr(pipe_mod, "ensure_privacy_checkbox_checked_multimodal", fake_ensure)
    monkeypatch.setattr(pipe_mod, "run_full_server_selector_check", fake_server_check)

    async def _run() -> None:
        result, tapped = await run_full_server_selector_check_with_privacy_precheck(
            MagicMock(),
            Path("/tmp"),
            MagicMock(),
            privacy_checkbox_already_tapped=False,
        )
        assert tapped is True
        assert result.ok is True
        assert "[PrivacyCheckbox] VERIFIED" in result.message
        assert ensure_calls == [False]

    asyncio.run(_run())


def test_retry_checkbox_when_list_not_opened_and_not_yet_tapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from game_agent.services import server_selector_pipeline as pipe_mod

    server_calls = 0

    async def fake_ensure(_adb, _root, **kwargs):
        from game_agent.services.privacy_checkbox import PrivacyCheckboxEnsureResult

        prefix = kwargs.get("prefix", "")
        if prefix == "privacy_cb_precheck":
            return PrivacyCheckboxEnsureResult(
                action="skipped",
                message="[PrivacyCheckbox] SKIPPED — no terms",
                tapped=False,
            )
        if prefix == "privacy_cb_retry":
            return PrivacyCheckboxEnsureResult(
                action="tapped",
                message="[PrivacyCheckbox] VERIFIED on retry",
                tapped=True,
                verified=True,
            )
        return PrivacyCheckboxEnsureResult(action="skipped", message="skip", tapped=False)

    async def fake_server_check(*_args, **_kwargs):
        nonlocal server_calls
        server_calls += 1
        if server_calls == 1:
            return ServerSelectorCheckResult(
                ok=False,
                message="[ServerCheck] FAILED — server list panel did not open on same screen.",
                taps_used=3,
                panel_opened=False,
            )
        return ServerSelectorCheckResult(
            ok=True,
            message="[ServerCheck] PASSED on retry",
            taps_used=1,
            panel_opened=True,
        )

    monkeypatch.setattr(pipe_mod, "ensure_privacy_checkbox_checked_multimodal", fake_ensure)
    monkeypatch.setattr(pipe_mod, "run_full_server_selector_check", fake_server_check)

    async def _run() -> None:
        result, tapped = await run_full_server_selector_check_with_privacy_precheck(
            MagicMock(),
            Path("/tmp"),
            MagicMock(),
            privacy_checkbox_already_tapped=False,
        )
        assert tapped is True
        assert result.ok is True
        assert server_calls == 2
        assert "retry after privacy checkbox tap" in result.message

    asyncio.run(_run())


def test_no_retry_tap_when_precheck_already_tapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from game_agent.services import server_selector_pipeline as pipe_mod

    ensure_calls = 0

    async def fake_ensure(_adb, _root, **_kwargs):
        nonlocal ensure_calls
        ensure_calls += 1
        from game_agent.services.privacy_checkbox import PrivacyCheckboxEnsureResult

        return PrivacyCheckboxEnsureResult(
            action="tapped",
            message="[PrivacyCheckbox] VERIFIED",
            tapped=True,
            verified=True,
        )

    async def fake_server_check(*_args, **_kwargs):
        return ServerSelectorCheckResult(
            ok=False,
            message="[ServerCheck] FAILED [E2006] — empty server slot and list panel did not open after tap verification.",
            taps_used=3,
            panel_opened=False,
        )

    monkeypatch.setattr(pipe_mod, "ensure_privacy_checkbox_checked_multimodal", fake_ensure)
    monkeypatch.setattr(pipe_mod, "run_full_server_selector_check", fake_server_check)

    async def _run() -> None:
        result, tapped = await run_full_server_selector_check_with_privacy_precheck(
            MagicMock(),
            Path("/tmp"),
            MagicMock(),
            privacy_checkbox_already_tapped=False,
        )
        assert tapped is True
        assert result.ok is False
        assert ensure_calls == 1
        assert "retry after privacy checkbox tap" not in result.message

    asyncio.run(_run())
