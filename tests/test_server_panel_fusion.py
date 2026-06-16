from game_agent.models.server_panel_vision import ServerPanelVisionVerdict
from game_agent.services.server_panel_fusion import fuse_panel_verdict
from game_agent.services.server_panel_verify import parse_server_panel_vision
from game_agent.services.server_selector_check import PanelOcrVerdict


def test_parse_server_panel_vision_json() -> None:
    raw = """
    {
      "server_list_panel_open": true,
      "same_screen_enter_cta": true,
      "confidence": 0.92,
      "reason": "选择区服 modal visible"
    }
    """
    v = parse_server_panel_vision(raw)
    assert v.passed is True
    assert v.same_screen is True
    assert v.confidence == 0.92


def test_fusion_both_pass() -> None:
    ocr = PanelOcrVerdict(passed=True, evidence="modal_title")
    vision = ServerPanelVisionVerdict(
        passed=True, same_screen=True, confidence=0.9, reason="panel open"
    )
    r = fuse_panel_verdict(ocr=ocr, vision=vision)
    assert r.passed is True
    assert r.source == "both"


def test_fusion_vision_salvage() -> None:
    ocr = PanelOcrVerdict(passed=False, evidence="no_modal_evidence")
    vision = ServerPanelVisionVerdict(
        passed=True, same_screen=True, confidence=0.88, reason="选择区服"
    )
    r = fuse_panel_verdict(ocr=ocr, vision=vision)
    assert r.passed is True
    assert r.source == "vision"


def test_fusion_vision_veto() -> None:
    ocr = PanelOcrVerdict(passed=True, evidence="modal_title")
    vision = ServerPanelVisionVerdict(
        passed=False, same_screen=True, confidence=0.9, reason="OCR jitter only"
    )
    r = fuse_panel_verdict(ocr=ocr, vision=vision)
    assert r.passed is False
    assert r.source == "vision_veto"


def test_fusion_both_fail() -> None:
    ocr = PanelOcrVerdict(passed=False, evidence="no_modal_evidence")
    vision = ServerPanelVisionVerdict(
        passed=False, same_screen=False, confidence=0.2, reason="no panel"
    )
    r = fuse_panel_verdict(ocr=ocr, vision=vision)
    assert r.passed is False
    assert r.source == "fail"


def test_fusion_ocr_only_when_no_vision() -> None:
    ocr = PanelOcrVerdict(passed=True, evidence="modal_title")
    r = fuse_panel_verdict(ocr=ocr, vision=None)
    assert r.passed is True
    assert r.source == "ocr_only"


def test_fusion_hard_veto_page_navigation() -> None:
    ocr = PanelOcrVerdict(
        passed=False, evidence="page_navigation", page_navigation=True
    )
    vision = ServerPanelVisionVerdict(
        passed=True, same_screen=True, confidence=0.95, reason="panel"
    )
    r = fuse_panel_verdict(ocr=ocr, vision=vision)
    assert r.passed is False
    assert r.source == "hard_veto"


def test_fusion_16914_junk_ocr_with_vision_veto() -> None:
    """单字 OCR 抖动：若 OCR 误 pass，Vision 可否决。"""
    ocr = PanelOcrVerdict(passed=True, evidence="close_plus_new_rows")
    vision = ServerPanelVisionVerdict(
        passed=False, same_screen=True, confidence=0.85, reason="no server modal"
    )
    r = fuse_panel_verdict(ocr=ocr, vision=vision)
    assert r.passed is False
    assert r.source == "vision_veto"
