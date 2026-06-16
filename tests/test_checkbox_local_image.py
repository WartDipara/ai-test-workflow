"""tests/img/before.png：复现 ensure 点击前的定位路径，并用多模态验证 tap 是否对准 checkbox。

与 ensure_privacy_checkbox_checked_multimodal 在 adb.tap 之前一致：
  screencap(图片) → extract_text_with_bbox → locate_privacy_checkbox
  → checkbox_roi_box → mark_checkbox_tap_on_image
多模态判定在测试内直接调用 VisionWorker，不经过流水线包装。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from game_agent.services.checkbox_locator import CheckboxLocateResult, locate_privacy_checkbox
from game_agent.services.privacy_checkbox import checkbox_roi_box, mark_checkbox_tap_on_image
from game_agent.utils.ocr_util import extract_text_with_bbox, extract_text_with_bounds
from game_agent.workers.vision_worker import VisionWorker
from tests.checkbox_images import CHECKBOX_BEFORE, require_checkbox_images

if TYPE_CHECKING:
    from game_agent.models.settings import LLMSection

_ROOT = Path(__file__).resolve().parents[1]
_MARKED_OUTPUT = _ROOT / "artifacts" / "checkbox_debug" / "image_checkbox_marked.png"
_SETTINGS = _ROOT / "config" / "settings.yaml"
_VISION_MIN_CONFIDENCE = 0.6


def _load_multimodal_cfg() -> LLMSection | None:
    if not _SETTINGS.is_file():
        return None
    from game_agent.config.loader import load_app_config

    cfg = load_app_config(_SETTINGS)
    return cfg.llm_multimodal or cfg.llm


def _locate_and_mark_like_ensure(
    image_path: Path,
    marked_path: Path,
    *,
    step: int = 0,
) -> tuple[CheckboxLocateResult, Path, str] | None:
    """与 ensure 在 adb.tap 前相同的 OCR → locate → 标红点。"""
    with Image.open(image_path) as im:
        sw, sh = im.size

    bboxes = extract_text_with_bbox(image_path, device_w=sw, device_h=sh)
    if not bboxes:
        return None

    located = locate_privacy_checkbox(bboxes, sw, sh, step=step)
    if located is None:
        return None

    box = checkbox_roi_box(located, sw, sh)
    marked = mark_checkbox_tap_on_image(
        image_path,
        marked_path,
        cx=located.cx,
        cy=located.cy,
        roi_box=box,
    )
    ocr_summary = extract_text_with_bounds(image_path, device_w=sw, device_h=sh)
    return located, marked, ocr_summary


@pytest.fixture(scope="module", autouse=True)
def _checkbox_fixture_images() -> None:
    require_checkbox_images()


def test_privacy_checkbox_locate_tap_alignment_on_image() -> None:
    llm_cfg = _load_multimodal_cfg()
    if llm_cfg is None:
        pytest.skip("config/settings.yaml not found")

    result = _locate_and_mark_like_ensure(CHECKBOX_BEFORE, _MARKED_OUTPUT, step=0)
    assert result is not None, (
        "OCR/locate failed on tests/img/before.png — "
        "same path as ensure_privacy_checkbox_checked_multimodal before tap"
    )
    located, marked, ocr_summary = result
    assert marked.is_file() and marked.stat().st_size > 0

    async def _judge():
        vision = VisionWorker(llm_cfg)
        return await vision.judge_checkbox_tap_alignment(
            screenshot_path=marked,
            tap_x=located.cx,
            tap_y=located.cy,
            ocr_summary=ocr_summary,
        )

    judgment = asyncio.run(_judge())
    assert judgment.on_checkbox, (
        f"tap ({located.cx},{located.cy}) not on checkbox — "
        f"adjust={judgment.adjust_direction!r} conf={judgment.confidence:.2f} "
        f"reason={judgment.reason!r} marked={_MARKED_OUTPUT}"
    )
    assert judgment.confidence >= _VISION_MIN_CONFIDENCE
