"""adaptive 动态树去重纯逻辑测试。"""

from __future__ import annotations

from game_agent.models.launch_graph_state import empty_launch_graph_state, mark_node_done
from game_agent.models.phase_template import PhaseSpec
from game_agent.services.adaptive_tree import DecideOutcomeKind, decide_phase_spec, materialize_tree_node


def test_materialize_after_completed_is_blocked_by_decide() -> None:
    state = empty_launch_graph_state()
    spec = PhaseSpec(phase_id="class_select", phase_label="职业选择", confidence=0.9)
    mark_node_done(state, spec.node_id())
    outcome = decide_phase_spec(state, spec, min_confidence=0.55)
    assert outcome.kind == DecideOutcomeKind.REJECT_DUPLICATE
    assert state.get("adaptive_active_node_id") == ""


def test_materialize_different_phase_after_one_done() -> None:
    state = empty_launch_graph_state()
    first = PhaseSpec(phase_id="class_select", phase_label="职业选择", confidence=0.9)
    mark_node_done(state, first.node_id())
    second = PhaseSpec(phase_id="confirm_next", phase_label="下一步", confidence=0.9)
    outcome = decide_phase_spec(state, second, min_confidence=0.55)
    assert outcome.kind == DecideOutcomeKind.ACCEPT
    node = materialize_tree_node(state, outcome.spec, entry_fingerprint="fp", created_round=2)
    assert node.node_id == "adaptive.confirm_next"
