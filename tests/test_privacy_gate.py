from __future__ import annotations

from game_agent.graphs.launch_facts import classify_screen_facts
from game_agent.graphs.launch_routing import route_next
from game_agent.models.launch_graph_state import LaunchFacts, LaunchGraphState
from game_agent.models.privacy_gate import PrivacyGateJudgment
from game_agent.services.privacy_gate import (
    merge_privacy_gate_judgment,
    ocr_has_privacy_context,
    pick_consent_button_from_ocr,
    privacy_gate_vlm_unavailable,
    should_invoke_privacy_gate_vlm,
)
from game_agent.utils.ocr_util import OcrBbox


def _state(**kwargs) -> LaunchGraphState:
    base: LaunchGraphState = {
        "facts": {},
        "completed_nodes": {},
        "failed_nodes": {},
    }
    base.update(kwargs)  # type: ignore[typeddict-item]
    if isinstance(base.get("facts"), LaunchFacts):
        base["facts"] = base["facts"].model_dump()
    return base


def test_classify_screen_facts_does_not_set_privacy_branch_fields() -> None:
    """OCR 阶段不写隐私分支，避免正则误判驱动路由。"""
    bboxes = [
        OcrBbox(text="已阅读并同意", cx=400, cy=1800, x1=0, y1=0, x2=0, y2=0),
        OcrBbox(text="不同意", cx=200, cy=2200, x1=0, y1=0, x2=0, y2=0),
        OcrBbox(text="同意并进入", cx=880, cy=2200, x1=0, y1=0, x2=0, y2=0),
    ]
    ocr = "已阅读并同意 用户协议 隐私政策 不同意 同意并进入"
    facts = classify_screen_facts(bboxes, screen_w=1080, screen_h=2400, ocr_summary=ocr)
    assert facts.initial_privacy_dialog is False
    assert facts.terms_checkbox_visible is False
    assert facts.agree_button_xy is None
    assert "privacy_context_detected" in facts.classify_reason


def test_ocr_has_privacy_context() -> None:
    assert ocr_has_privacy_context("已阅读并同意 用户协议 隐私政策")
    assert not ocr_has_privacy_context("账号 密码 登录")


def test_should_invoke_vlm_when_privacy_text_present() -> None:
    facts = LaunchFacts()
    ocr = "已阅读并同意 用户协议 隐私政策 不同意 同意并进入"
    assert should_invoke_privacy_gate_vlm(facts, ocr_merged=ocr) is True


def test_should_not_invoke_vlm_without_privacy_text() -> None:
    facts = LaunchFacts()
    assert should_invoke_privacy_gate_vlm(facts, ocr_merged="账号 密码 登录") is False


def test_should_not_invoke_vlm_when_login_blocking() -> None:
    facts = LaunchFacts(login_blocking=True)
    ocr = "已阅读并同意 用户协议"
    assert should_invoke_privacy_gate_vlm(facts, ocr_merged=ocr) is False


def test_should_not_invoke_vlm_when_milestones_done() -> None:
    facts = LaunchFacts()
    ocr = "已阅读并同意 用户协议"
    assert (
        should_invoke_privacy_gate_vlm(
            facts,
            ocr_merged=ocr,
            privacy_milestones_done=True,
        )
        is False
    )


def test_vlm_unavailable_does_not_guess_branch() -> None:
    facts = LaunchFacts(classify_reason="privacy_context_detected")
    unavailable = privacy_gate_vlm_unavailable(facts)
    assert unavailable.privacy_gate_kind == "unknown"
    assert unavailable.initial_privacy_dialog is False
    assert unavailable.terms_checkbox_visible is False
    assert unavailable.agree_button_xy is None


def test_merge_modal_clears_checkbox_and_sets_agree_xy() -> None:
    facts = LaunchFacts(classify_reason="privacy_context_detected")
    bboxes = [
        OcrBbox(text="不同意", cx=200, cy=2200, x1=0, y1=0, x2=0, y2=0),
        OcrBbox(text="同意并进入", cx=744, cy=1713, x1=0, y1=0, x2=0, y2=0),
    ]
    judgment = PrivacyGateJudgment(
        gate_kind="modal",
        confidence=0.92,
        tap_x=744,
        tap_y=1713,
        tap_label="同意并进入",
        reason="bottom consent buttons",
    )
    merged = merge_privacy_gate_judgment(facts, judgment, bboxes=bboxes)
    assert merged.privacy_gate_kind == "modal"
    assert merged.initial_privacy_dialog is True
    assert merged.terms_checkbox_visible is False
    assert merged.agree_button_xy == (744, 1713)


def test_merge_checkbox_keeps_checkbox_route() -> None:
    facts = LaunchFacts(classify_reason="privacy_context_detected")
    judgment = PrivacyGateJudgment(
        gate_kind="checkbox",
        confidence=0.88,
        reason="login form checkbox row",
    )
    merged = merge_privacy_gate_judgment(facts, judgment, bboxes=[])
    assert merged.privacy_gate_kind == "checkbox"
    assert merged.initial_privacy_dialog is False
    assert merged.terms_checkbox_visible is True
    assert merged.agree_button_xy is None


def test_merge_unknown_clears_privacy_branch_fields() -> None:
    facts = LaunchFacts(classify_reason="privacy_context_detected")
    judgment = PrivacyGateJudgment(
        gate_kind="unknown",
        confidence=0.3,
        reason="ambiguous",
    )
    merged = merge_privacy_gate_judgment(facts, judgment, bboxes=[])
    assert merged.privacy_gate_kind == "unknown"
    assert merged.initial_privacy_dialog is False
    assert merged.terms_checkbox_visible is False


def test_pick_consent_button_from_ocr() -> None:
    bboxes = [
        OcrBbox(text="不同意", cx=200, cy=2200, x1=0, y1=0, x2=0, y2=0),
        OcrBbox(text="同意并进入", cx=880, cy=2200, x1=0, y1=0, x2=0, y2=0),
    ]
    picked = pick_consent_button_from_ocr(bboxes)
    assert picked == (880, 2200, "同意并进入")


def test_route_modal_after_gate_merge_not_checkbox() -> None:
    facts = LaunchFacts(
        initial_privacy_dialog=True,
        agree_button_xy=(744, 1713),
        terms_checkbox_visible=False,
        privacy_gate_kind="modal",
        enter_cta_visible=True,
        enter_cta_xy=(400, 800),
    )
    state = _state(facts=facts, privacy_checked=False)
    assert route_next(state) == "handle_initial_privacy_dialog"
