from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from game_agent.models.privacy_checkbox_judgment import PrivacyCheckboxJudgment
from game_agent.services.privacy_checkbox import (
    ensure_privacy_checkbox_checked_multimodal,
    mark_checkbox_tap_on_image,
)
from tests.checkbox_images import (
    CHECKBOX_AFTER_CHECKED,
    CHECKBOX_BEFORE,
    SCREEN_H,
    SCREEN_W,
    checkbox_roi_from_before,
    copy_checkbox_screencap,
    require_checkbox_images,
)


@pytest.fixture(scope="module", autouse=True)
def _checkbox_fixture_images() -> None:
    require_checkbox_images()


def test_multimodal_already_checked_before_tap_skips_tap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = MagicMock()
    adb.touch_size.return_value = (SCREEN_W, SCREEN_H)

    def fake_screencap(path: Path) -> None:
        copy_checkbox_screencap(path, CHECKBOX_BEFORE)

    adb.screencap_png.side_effect = fake_screencap

    monkeypatch.setattr(
        "game_agent.services.privacy_checkbox.extract_text_with_bounds",
        lambda *_a, **_k: "ocr",
    )
    monkeypatch.setattr(
        "game_agent.services.privacy_checkbox.mark_checkbox_tap_on_image",
        lambda src, dst, **kw: dst,
    )

    mock_worker = MagicMock()
    mock_worker.judge_privacy_checkbox_state = AsyncMock(
        return_value=PrivacyCheckboxJudgment(
            state="checked",
            confidence=0.9,
            checkbox_visible=True,
            reason="already selected",
        )
    )
    monkeypatch.setattr(
        "game_agent.services.privacy_checkbox.VisionWorker",
        lambda *_a, **_k: mock_worker,
    )

    async def _run() -> None:
        result = await ensure_privacy_checkbox_checked_multimodal(
            adb,
            tmp_path,
            llm_cfg=MagicMock(),
            prefix="test_mm",
        )
        assert result.action == "already_checked"
        assert result.verified is True
        assert result.tapped is False
        adb.tap.assert_not_called()

    asyncio.run(_run())


def test_multimodal_verified_after_tap_by_vision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = MagicMock()
    adb.touch_size.return_value = (SCREEN_W, SCREEN_H)
    adb.tap.return_value = "Tapped"

    screencap_calls = {"n": 0}

    def fake_screencap(path: Path) -> None:
        screencap_calls["n"] += 1
        source = CHECKBOX_BEFORE if screencap_calls["n"] == 1 else CHECKBOX_AFTER_CHECKED
        copy_checkbox_screencap(path, source)

    adb.screencap_png.side_effect = fake_screencap

    monkeypatch.setattr(
        "game_agent.services.privacy_checkbox.extract_text_with_bounds",
        lambda *_a, **_k: "ocr",
    )
    monkeypatch.setattr(
        "game_agent.services.privacy_checkbox.mark_checkbox_tap_on_image",
        lambda src, dst, **kw: dst,
    )

    mock_worker = MagicMock()
    mock_worker.judge_privacy_checkbox_state = AsyncMock(
        side_effect=[
            PrivacyCheckboxJudgment(state="unchecked", confidence=0.85, checkbox_visible=True),
            PrivacyCheckboxJudgment(state="checked", confidence=0.88, checkbox_visible=True),
        ]
    )
    monkeypatch.setattr(
        "game_agent.services.privacy_checkbox.VisionWorker",
        lambda *_a, **_k: mock_worker,
    )

    async def _run() -> None:
        result = await ensure_privacy_checkbox_checked_multimodal(
            adb,
            tmp_path,
            llm_cfg=MagicMock(),
            prefix="test_mm",
        )
        assert result.action == "tapped"
        assert result.verified is True
        adb.tap.assert_called_once()

    asyncio.run(_run())


def test_multimodal_falls_back_to_sync_when_no_llm(tmp_path: Path) -> None:
    adb = MagicMock()

    async def _run() -> None:
        with patch(
            "game_agent.services.privacy_checkbox.ensure_privacy_checkbox_checked",
            return_value=MagicMock(action="skipped", verified=True, message="sync"),
        ) as sync_mock:
            result = await ensure_privacy_checkbox_checked_multimodal(
                adb,
                tmp_path,
                llm_cfg=None,
            )
            sync_mock.assert_called_once()
            assert result.message == "sync"

    asyncio.run(_run())


def test_mark_checkbox_tap_on_image_writes_file(tmp_path: Path) -> None:
    located, box = checkbox_roi_from_before()
    dst = tmp_path / "marked.png"
    out = mark_checkbox_tap_on_image(
        CHECKBOX_BEFORE,
        dst,
        cx=located.cx,
        cy=located.cy,
        roi_box=box,
    )
    assert out.is_file()
    assert out.stat().st_size > 0
