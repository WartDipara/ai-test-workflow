"""Checkbox 单元测试共用截图：tests/img/before.png、after.png、after_2.png。"""

from __future__ import annotations

import shutil
from pathlib import Path

from game_agent.services.checkbox_locator import CheckboxLocateResult, locate_privacy_checkbox
from game_agent.services.privacy_checkbox import checkbox_roi_box, roi_mean_abs_diff
from game_agent.utils.ocr_util import extract_text_with_bbox

_IMG_DIR = Path(__file__).resolve().parent / "img"
CHECKBOX_BEFORE = _IMG_DIR / "before.png"
CHECKBOX_AFTER_CHECKED = _IMG_DIR / "after.png"
CHECKBOX_AFTER_UNCHECKED = _IMG_DIR / "after_2.png"

SCREEN_W = 1080
SCREEN_H = 2400
ROI_CHANGE_THRESHOLD = 6.0

ALL_CHECKBOX_IMAGES = (
    CHECKBOX_BEFORE,
    CHECKBOX_AFTER_CHECKED,
    CHECKBOX_AFTER_UNCHECKED,
)


def require_checkbox_images() -> None:
    missing = [p for p in ALL_CHECKBOX_IMAGES if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing checkbox fixture images: {missing}")


def locate_checkbox_on_image(
    image_path: Path,
    *,
    step: int = 0,
) -> CheckboxLocateResult | None:
    """对 fixture 截图跑 OCR → locate（与 ensure 点击前一致）。"""
    bboxes = extract_text_with_bbox(image_path, device_w=SCREEN_W, device_h=SCREEN_H)
    if not bboxes:
        return None
    return locate_privacy_checkbox(bboxes, SCREEN_W, SCREEN_H, step=step)


def checkbox_roi_from_before(*, step: int = 0) -> tuple[CheckboxLocateResult, tuple[int, int, int, int]]:
    located = locate_checkbox_on_image(CHECKBOX_BEFORE, step=step)
    if located is None:
        raise AssertionError("OCR/locate failed on tests/img/before.png")
    box = checkbox_roi_box(located, SCREEN_W, SCREEN_H)
    return located, box


def roi_diff_vs_before(candidate: Path, *, step: int = 0) -> float:
    _, box = checkbox_roi_from_before(step=step)
    return roi_mean_abs_diff(CHECKBOX_BEFORE, candidate, box)


def copy_checkbox_screencap(dest: Path, source: Path) -> None:
    """模拟 adb.screencap_png：将 fixture 复制到 artifact 路径。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, dest)
