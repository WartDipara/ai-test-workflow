from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from game_agent.models.settings import MolmopointSection
from game_agent.services.checkbox_locator import (
    find_privacy_terms_anchor,
    locate_checkbox_tap,
    locate_checkbox_via_molmopoint,
    pick_best_molmopoint_point,
    validate_molmopoint_point,
)
from game_agent.utils.ocr_util import OcrBbox, extract_text_with_bbox
from tests.checkbox_images import (
    CHECKBOX_AFTER_CHECKED,
    CHECKBOX_AFTER_UNCHECKED,
    CHECKBOX_BEFORE,
    ROI_CHANGE_THRESHOLD,
    SCREEN_H,
    SCREEN_W,
    copy_checkbox_screencap,
    locate_checkbox_on_image,
    require_checkbox_images,
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


def _default_cfg() -> MolmopointSection:
    return MolmopointSection(base_url="http://127.0.0.1:8000")


def test_validate_molmopoint_point_left_and_vertical() -> None:
    bboxes = [_bbox("已阅读并同意隐私政策", 800, 900, 1200, 940)]
    anchor = find_privacy_terms_anchor(bboxes)
    assert anchor is not None
    cfg = _default_cfg()
    assert validate_molmopoint_point(750, anchor.cy, anchor, cfg) is True
    assert validate_molmopoint_point(810, anchor.cy, anchor, cfg) is False
    assert validate_molmopoint_point(750, anchor.cy + 80, anchor, cfg) is False


def test_pick_best_molmopoint_point_prefers_vertical_alignment() -> None:
    bboxes = [_bbox("已阅读并同意隐私政策", 800, 900, 1200, 940)]
    anchor = find_privacy_terms_anchor(bboxes)
    assert anchor is not None
    cfg = _default_cfg()
    points = [(700, anchor.cy + 30), (720, anchor.cy + 2)]
    best = pick_best_molmopoint_point(points, anchor, cfg)
    assert best == (720, anchor.cy + 2)


def test_locate_checkbox_tap_uses_molmopoint_when_valid(tmp_path: Path) -> None:
    require_checkbox_images()
    located = locate_checkbox_on_image(CHECKBOX_BEFORE)
    assert located is not None
    bboxes = extract_text_with_bbox(CHECKBOX_BEFORE, device_w=SCREEN_W, device_h=SCREEN_H)
    assert bboxes
    anchor = find_privacy_terms_anchor(bboxes)
    assert anchor is not None
    cfg = _default_cfg()
    molmo_x = float(located.cx)
    molmo_y = float(located.cy)

    with patch(
        "game_agent.services.molmopoint_client.predict_points",
        return_value=[(molmo_x, molmo_y)],
    ):
        result = locate_checkbox_tap(
            bboxes,
            SCREEN_W,
            SCREEN_H,
            image_path=CHECKBOX_BEFORE,
            molmopoint_cfg=cfg,
            step=0,
            try_molmopoint=True,
        )
    assert result is not None
    assert result.locate_method == "molmopoint"
    assert result.cx == int(molmo_x)


def test_locate_checkbox_tap_falls_back_to_ocr_offset(tmp_path: Path) -> None:
    require_checkbox_images()
    bboxes = extract_text_with_bbox(CHECKBOX_BEFORE, device_w=SCREEN_W, device_h=SCREEN_H)
    assert bboxes
    cfg = _default_cfg()

    with patch("game_agent.services.molmopoint_client.predict_points", return_value=[(2000.0, 500.0)]):
        result = locate_checkbox_tap(
            bboxes,
            SCREEN_W,
            SCREEN_H,
            image_path=CHECKBOX_BEFORE,
            molmopoint_cfg=cfg,
            step=0,
            try_molmopoint=True,
        )
    assert result is not None
    assert result.locate_method == "ocr_offset"
    located = locate_checkbox_on_image(CHECKBOX_BEFORE)
    assert located is not None
    assert result.cx < located.line_x1


def test_molmopoint_disabled_skips_request() -> None:
    require_checkbox_images()
    bboxes = extract_text_with_bbox(CHECKBOX_BEFORE, device_w=SCREEN_W, device_h=SCREEN_H)
    assert bboxes
    anchor = find_privacy_terms_anchor(bboxes)
    assert anchor is not None
    cfg = MolmopointSection(base_url="", enabled=False)
    with patch("game_agent.services.molmopoint_client.predict_points") as mock_predict:
        result = locate_checkbox_via_molmopoint(
            anchor,
            CHECKBOX_BEFORE,
            SCREEN_W,
            SCREEN_H,
            cfg,
        )
    mock_predict.assert_not_called()
    assert result is None


@pytest.fixture(scope="module", autouse=True)
def _checkbox_fixture_images() -> None:
    require_checkbox_images()


def test_ensure_privacy_checkbox_molmopoint_once_then_offset(tmp_path: Path) -> None:
    from game_agent.services.privacy_checkbox import ensure_privacy_checkbox_checked

    adb = MagicMock()
    adb.touch_size.return_value = (SCREEN_W, SCREEN_H)

    screencap_calls = {"n": 0}

    def fake_screencap(path: Path) -> None:
        screencap_calls["n"] += 1
        if screencap_calls["n"] == 1:
            copy_checkbox_screencap(path, CHECKBOX_BEFORE)
        elif screencap_calls["n"] == 2:
            copy_checkbox_screencap(path, CHECKBOX_AFTER_UNCHECKED)
        else:
            copy_checkbox_screencap(path, CHECKBOX_AFTER_CHECKED)

    adb.screencap_png.side_effect = fake_screencap

    cfg = _default_cfg()
    predict_calls = {"n": 0}

    def fake_predict(_path, _cfg):
        predict_calls["n"] += 1
        return []

    with patch("game_agent.services.molmopoint_client.predict_points", side_effect=fake_predict):
        result = ensure_privacy_checkbox_checked(
            adb,
            tmp_path,
            prefix="molmo_cb",
            max_steps=1,
            molmopoint_cfg=cfg,
        )

    assert predict_calls["n"] == 1
    assert result.verified is True
    assert result.locate is not None
    assert result.roi_diff >= ROI_CHANGE_THRESHOLD
