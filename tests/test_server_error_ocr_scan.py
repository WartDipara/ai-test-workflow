from game_agent.services.server_error_ocr_scan import (
    find_server_error_text,
    probe_from_server_error_ocr,
)
from game_agent.services.server_vision_probe import merge_ocr_server_error, parse_server_connectivity_probe
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


def test_find_server_error_default_server_toast() -> None:
    bboxes = [_bbox("默认服不存在，请重新选服", 900, 200, 1500, 260)]
    assert find_server_error_text(bboxes) == "默认服不存在，请重新选服"


def test_find_server_error_ocr_garbled_variant() -> None:
    """16914 回归：OCR 误识「双认服不存在」。"""
    bboxes = [_bbox("双认服不存在，请重新选服", 900, 200, 1500, 260)]
    assert find_server_error_text(bboxes) is not None
    probe = probe_from_server_error_ocr(bboxes)
    assert probe is not None
    assert probe.recommendation == "fail_fast"
    assert probe.server_slot_status == "error"
    assert probe.has_network_error_ui is True


def test_merge_ocr_overrides_optimistic_vision() -> None:
    vision_probe = parse_server_connectivity_probe(
        '{"on_enter_game_screen":true,"enter_button_visible":true,'
        '"server_slot_status":"empty","has_network_error_ui":false,'
        '"recommendation":"tap_verify","confidence":0.95,'
        '"reason":"accessible but not picked"}'
    )
    bboxes = [_bbox("默认服不存在，请重新选服", 900, 200, 1500, 260)]
    merged = merge_ocr_server_error(vision_probe, bboxes)
    assert merged.recommendation == "fail_fast"
    assert merged.server_slot_status == "error"
    assert merged.has_network_error_ui is True


def test_no_error_on_clean_empty_slot_bboxes() -> None:
    """无 error toast 时仅 empty 占位，不触发 OCR fail_fast。"""
    bboxes = [
        _bbox("踏入仙途", 1100, 770, 1300, 820),
        _bbox("----", 1050, 620, 1150, 660),
        _bbox("Click to select Server", 1160, 620, 1400, 660),
    ]
    assert find_server_error_text(bboxes) is None
    assert probe_from_server_error_ocr(bboxes) is None
