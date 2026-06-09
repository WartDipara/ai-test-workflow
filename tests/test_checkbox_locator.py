from game_agent.services.checkbox_locator import checkbox_tap_x, locate_privacy_checkbox
from game_agent.utils.ocr_util import OcrBbox


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
    assert r0.half_char_px == 400 // 10 // 2
    assert r0.cx == 800 - r0.half_char_px
    assert r1.cx == 800 - r0.half_char_px * 2


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
    """retry_1_20260609：最左段 8 字、框宽 250 → 半字 15；step=2 接近实测 checkbox。"""
    bboxes = [
        _bbox("已经详编阅读并同", 883, 916, 1133, 956),
        _bbox("许可及服务协议及", 1341, 918, 1800, 958),
    ]
    r0 = locate_privacy_checkbox(bboxes, 2400, 1080, step=0)
    r2 = locate_privacy_checkbox(bboxes, 2400, 1080, step=2)
    assert r0 is not None and r2 is not None
    assert r0.half_char_px == 15
    assert r0.cx == 868
    assert r2.cx == 838


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


def test_step1_from_cache_uses_prefix_anchor() -> None:
    """detect_checkbox 缓存后 step=1 应沿前缀锚点左移，而非重 OCR 命中链接。"""
    half_char = 13
    line_x1 = 880
    assert checkbox_tap_x(line_x1, half_char, 1) == 854


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
