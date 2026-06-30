"""对话暗色区域定位单测。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from game_agent.services.dialogue_dim_locator import locate_dialogue_dim_regions
from game_agent.utils.ocr_util import run_ocr_frame

SCREENSHOT = Path(__file__).resolve().parents[1] / "screenshot_20260626_152742.png"


@pytest.mark.skipif(not SCREENSHOT.is_file(), reason="screenshot fixture missing")
def test_dim_locator_finds_dark_region_away_from_continue_text() -> None:
    _, bboxes = run_ocr_frame(SCREENSHOT, device_w=1080, device_h=2400)
    result = locate_dialogue_dim_regions(
        SCREENSHOT,
        bboxes=bboxes,
        screen_w=1080,
        screen_h=2400,
    )
    assert result.recommended is not None
    assert result.recommended.area_ratio >= 0.05

    tap_x, tap_y = result.recommended.cx, result.recommended.cy
    for bbox in bboxes:
        text = (bbox.text or "").lower()
        if "continue" not in text and "blank" not in text:
            continue
        assert not (
            bbox.x1 <= tap_x <= bbox.x2 and bbox.y1 <= tap_y <= bbox.y2
        ), f"tap ({tap_x},{tap_y}) inside CTA bbox {bbox.text!r}"

    gray = cv2.cvtColor(cv2.imread(str(SCREENSHOT)), cv2.COLOR_BGR2GRAY)
    assert int(gray[tap_y, tap_x]) < result.dark_threshold

    assert tap_y > 1500, "should prefer lower dark blank area over CTA text row"


def test_dim_locator_no_image_returns_empty() -> None:
    result = locate_dialogue_dim_regions(Path("/nonexistent/image.png"))
    assert result.recommended is None
    assert not result.regions
