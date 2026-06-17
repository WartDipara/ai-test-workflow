"""PhaseEngine decide / materialize 辅助函数测试。"""

from __future__ import annotations

from game_agent.models.launch_graph_state import empty_launch_graph_state
from game_agent.models.phase_template import CompletionRule, PhaseSpec
from game_agent.services.adaptive_tree import (
    DecideOutcomeKind,
    decide_phase_spec,
    get_active_spec,
    materialize_tree_node,
)
from game_agent.services.phase_completion import evaluate_phase_complete


def test_evaluate_phase_complete_fingerprint() -> None:
    spec = PhaseSpec(
        phase_id="pick",
        complete=CompletionRule(kind="fingerprint_change"),
        action="tap_xy",
    )
    assert evaluate_phase_complete(
        spec,
        entry_fingerprint="a|游侠",
        after_fingerprint="b|下一步",
        ocr_summary="下一步",
        action_executed=True,
    )


def test_evaluate_phase_complete_ocr_hint() -> None:
    spec = PhaseSpec(
        phase_id="confirm",
        complete=CompletionRule(kind="ocr_contains", hint="下一步"),
        action="tap_xy",
    )
    assert evaluate_phase_complete(
        spec,
        entry_fingerprint="same",
        after_fingerprint="same",
        ocr_summary="神官 下一步",
        action_executed=True,
    )


def test_materialize_then_get_active_spec() -> None:
    state = empty_launch_graph_state()
    spec = PhaseSpec(
        phase_id="pick",
        phase_label="选择",
        action="tap_xy",
        x=100,
        y=200,
        confidence=0.85,
        complete=CompletionRule(kind="fingerprint_change"),
    )
    outcome = decide_phase_spec(state, spec, min_confidence=0.55)
    assert outcome.kind == DecideOutcomeKind.ACCEPT
    materialize_tree_node(state, outcome.spec, entry_fingerprint="a|x", created_round=1)
    active = get_active_spec(state)
    assert active is not None
    assert active.phase_id == "pick"
    assert active.x == 100
