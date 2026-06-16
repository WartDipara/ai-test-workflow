from __future__ import annotations

import pytest

from game_agent.services.checkbox_locator import (
    checkbox_tap_x,
    locate_privacy_checkbox,
)
from game_agent.utils.ocr_util import OcrBbox
from tests.checkbox_images import CHECKBOX_BEFORE, locate_checkbox_on_image, require_checkbox_images


def _bbox(
    text: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> OcrBbox:
    return OcrBbox(
        text=text,
        cx=(x1 + x2) // 2,
        cy=(y1 + y2) // 2,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
    )


def test_row_aggregation_uses_leftmost_not_link_text() -> None:
    """retry_1 场景：两段 OCR 在同一行，锚点应是最左段而非右侧协议链接。"""
    bboxes = [
        _bbox("已经详组阅法并同意", 1006, 916, 1300, 956),
        _bbox("使用许可及服务协议及", 1341, 918, 1800, 958),
    ]
    result = locate_privacy_checkbox(bboxes, 2400, 1080, step=0)
    assert result is not None
    assert result.line_x1 == 1006
    assert result.cx < 1006
    assert result.cx < 1341


def test_step_moves_further_left() -> None:
    bboxes = [_bbox("已阅读并同意隐私政策", 800, 900, 1200, 940)]
    r0 = locate_privacy_checkbox(bboxes, 2400, 1080, step=0)
    r1 = locate_privacy_checkbox(bboxes, 2400, 1080, step=1)
    assert r0 is not None and r1 is not None
    assert r1.cx < r0.cx
    assert r0.char_width_px == 40
    assert r0.base_offset_px == 73  # cw + gap(13) + box_half(20)
    assert r0.cx == 800 - r0.base_offset_px
    assert r1.cx == 800 - r0.base_offset_px - r0.char_width_px


def test_only_right_link_still_anchors_to_row_left() -> None:
    """仅命中右侧链接时，同行更左 bbox 仍应作为 line_x1。"""
    bboxes = [
        _bbox("已经详组阅法并同意", 1006, 916, 1290, 956),
        _bbox("许可及服务协议", 1341, 918, 1700, 958),
    ]
    result = locate_privacy_checkbox(bboxes, 2400, 1080, step=0)
    assert result is not None
    assert result.line_x1 == 1006
    assert result.cx < 1006


def test_retry1_half_char_steps() -> None:
    """retry_1_20260609：最左段 8 字、框宽 250 → char_w=31；step=2 再左移两字宽。"""
    bboxes = [
        _bbox("已经详编阅读并同", 883, 916, 1133, 956),
        _bbox("许可及服务协议及", 1341, 918, 1800, 958),
    ]
    r0 = locate_privacy_checkbox(bboxes, 2400, 1080, step=0)
    r2 = locate_privacy_checkbox(bboxes, 2400, 1080, step=2)
    assert r0 is not None and r2 is not None
    assert r0.char_width_px == 31
    assert r0.base_offset_px == 61  # cw + gap(10) + box_half(20)
    assert r0.cx == 822
    assert r2.cx == 760


def test_retry1_103053_prefix_row_not_link_row() -> None:
    """retry_1_20260609_103053：链接行 x1≈1280 不得压过左侧「已阅读」前缀行。"""
    bboxes = [
        _bbox("已经详组阅读并同", 880, 916, 1130, 956),
        _bbox("可及服务协议及", 1280, 918, 1480, 958),
        _bbox("通龄提示", 2260, 910, 2366, 950),
    ]
    r0 = locate_privacy_checkbox(bboxes, 2400, 1080, step=0)
    assert r0 is not None
    assert r0.line_x1 == 880
    assert r0.anchor_bbox_text == "已经详组阅读并同"
    assert r0.cx < 1000
    assert r0.cx != 1250


def test_step1_from_cache_uses_base_offset_and_char_width() -> None:
    """detect_checkbox 缓存后 step=1 应沿 base_offset + 一字宽左移。"""
    line_x1 = 880
    base_offset = 61
    char_w = 13
    assert checkbox_tap_x(
        line_x1, base_offset_px=base_offset, char_width_px=char_w, step=0
    ) == 819
    assert checkbox_tap_x(
        line_x1, base_offset_px=base_offset, char_width_px=char_w, step=1
    ) == 806


def test_english_i_have_read_and_agree() -> None:
    """16914 类英文协议行可定位 checkbox 左锚。"""
    bboxes = [_bbox("I have read and agree", 900, 880, 1300, 920)]
    result = locate_privacy_checkbox(bboxes, 2400, 1080, step=0)
    assert result is not None
    assert result.line_x1 == 900
    assert result.cx < 900
    assert "agree" in result.matched_line_text.lower()


def test_age_hint_not_merged_into_terms_row() -> None:
    """适龄图标与协议文字 y 接近但水平相距远，不应并入同一行锚点。"""
    bboxes = [
        _bbox("已经详组阅读并同", 880, 916, 1130, 956),
        _bbox("通龄提示", 2260, 910, 2366, 950),
    ]
    result = locate_privacy_checkbox(bboxes, 2400, 1080, step=0)
    assert result is not None
    assert result.line_x1 == 880
    assert "通龄" not in result.anchor_bbox_text


@pytest.fixture(scope="module", autouse=True)
def _checkbox_fixture_images() -> None:
    require_checkbox_images()


def test_locate_privacy_checkbox_on_fixture_before_image() -> None:
    located = locate_checkbox_on_image(CHECKBOX_BEFORE)
    assert located is not None
    assert located.cx > 0
    assert located.cy > 0
    assert located.cx < located.line_x1
