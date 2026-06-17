"""adaptive 动态树物化纯逻辑测试。"""

from __future__ import annotations

from game_agent.models.launch_graph_state import empty_launch_graph_state, mark_node_done
from game_agent.models.phase_template import CompletionRule, PhaseSpec
from game_agent.services.adaptive_tree import (
    DecideOutcomeKind,
    adaptive_tree_trace,
    commit_tree_node,
    completed_phases_summary,
    decide_phase_spec,
    get_active_tree_node,
    materialize_tree_node,
)


def _spec(phase_id: str = "list_pick", **kwargs) -> PhaseSpec:
    base = PhaseSpec(
        phase_id=phase_id,
        phase_label="职业选择",
        action="tap_xy",
        x=140,
        y=620,
        complete=CompletionRule(kind="fingerprint_change"),
        confidence=0.9,
    )
    return base.model_copy(update=kwargs)


def test_materialize_sets_active_and_tree() -> None:
    state = empty_launch_graph_state()
    spec = _spec()
    node = materialize_tree_node(state, spec, entry_fingerprint="fp1", created_round=1)
    assert node.status == "active"
    assert state["adaptive_active_node_id"] == "adaptive.list_pick"
    assert len(state.get("adaptive_phase_tree") or []) == 1
    assert get_active_tree_node(state) is not None
    assert "adaptive.list_pick[active]" in adaptive_tree_trace(state)


def test_commit_clears_active_and_marks_completed_nodes() -> None:
    state = empty_launch_graph_state()
    spec = _spec()
    node = materialize_tree_node(state, spec, entry_fingerprint="fp1", created_round=1)
    commit_tree_node(state, node, artifact="/tmp/a.png", evidence="tap ok")
    assert state.get("adaptive_active_node_id") == ""
    completed = state.get("completed_nodes") or {}
    assert completed["adaptive.list_pick"]["done"] is True
    tree = state.get("adaptive_phase_tree") or []
    assert tree[0]["status"] == "done"


def test_decide_rejects_duplicate_phase_id() -> None:
    state = empty_launch_graph_state()
    spec = _spec()
    mark_node_done(state, spec.node_id(), evidence="already done")
    outcome = decide_phase_spec(state, spec, min_confidence=0.55)
    assert outcome.kind == DecideOutcomeKind.REJECT_DUPLICATE


def test_decide_flow_done_when_inactive() -> None:
    state = empty_launch_graph_state()
    spec = _spec(flow_active=False, phase_id="skip")
    outcome = decide_phase_spec(state, spec, min_confidence=0.55)
    assert outcome.kind == DecideOutcomeKind.FLOW_DONE


def test_decide_downgrades_low_confidence_tap() -> None:
    state = empty_launch_graph_state()
    spec = _spec(confidence=0.2)
    outcome = decide_phase_spec(state, spec, min_confidence=0.55)
    assert outcome.kind == DecideOutcomeKind.ACCEPT
    assert outcome.spec is not None
    assert outcome.spec.action == "wait"


def test_completed_summary_includes_done_nodes() -> None:
    state = empty_launch_graph_state()
    spec = _spec()
    node = materialize_tree_node(state, spec, entry_fingerprint="fp1", created_round=1)
    commit_tree_node(state, node, artifact="", evidence="ok")
    summary = completed_phases_summary(state)
    assert "list_pick" in summary
    assert "[done]" in summary
