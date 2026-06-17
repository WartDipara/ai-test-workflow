"""CompletionRule 纯逻辑测试。"""

from __future__ import annotations

from game_agent.models.phase_template import CompletionRule, PhaseSpec, compute_phase_fingerprint


def test_fingerprint_change_rule() -> None:
    rule = CompletionRule(kind="fingerprint_change")
    assert rule.evaluate(
        entry_fingerprint="a",
        after_fingerprint="b",
        ocr_summary="",
        action="tap_xy",
        action_executed=True,
    )


def test_ocr_contains_rule() -> None:
    rule = CompletionRule(kind="ocr_contains", hint="下一步")
    assert rule.evaluate(
        entry_fingerprint="x",
        after_fingerprint="x",
        ocr_summary="职业 下一步 确认",
        action="tap_xy",
        action_executed=True,
    )


def test_always_after_wait_rule() -> None:
    rule = CompletionRule(kind="always_after_wait")
    assert rule.evaluate(
        entry_fingerprint="a",
        after_fingerprint="a",
        ocr_summary="",
        action="wait",
        action_executed=True,
    )
    assert not rule.evaluate(
        entry_fingerprint="a",
        after_fingerprint="a",
        ocr_summary="",
        action="tap_xy",
        action_executed=True,
    )


def test_phase_spec_node_id_slug() -> None:
    spec = PhaseSpec(phase_id="Class Select!", phase_label="职业选择")
    assert spec.node_id() == "adaptive.class_select"


def test_compute_phase_fingerprint() -> None:
    a = compute_phase_fingerprint(ocr_summary="游侠 剑士", phase_label="职业选择")
    b = compute_phase_fingerprint(ocr_summary="神官 下一步", phase_label="职业确认")
    assert a != b
