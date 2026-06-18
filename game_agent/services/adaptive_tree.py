"""adaptive 运行时动态树：物化、去重、完成态与摘要（纯逻辑）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from game_agent.models.launch_graph_state import LaunchGraphState, mark_node_done
from game_agent.models.phase_template import AdaptiveTreeNode, PhaseRecord, PhaseSpec


class DecideOutcomeKind(str, Enum):
    ACCEPT = "accept"
    FLOW_DONE = "flow_done"
    REJECT_DUPLICATE = "reject_duplicate"
    REJECT_INVALID = "reject_invalid"


@dataclass(frozen=True, slots=True)
class DecideOutcome:
    kind: DecideOutcomeKind
    spec: PhaseSpec | None = None
    reason: str = ""


def get_adaptive_tree(state: LaunchGraphState) -> list[AdaptiveTreeNode]:
    raw = state.get("adaptive_phase_tree") or []
    nodes: list[AdaptiveTreeNode] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            nodes.append(AdaptiveTreeNode.model_validate(item))
        except Exception:
            continue
    return nodes


def _set_adaptive_tree(state: LaunchGraphState, nodes: list[AdaptiveTreeNode]) -> None:
    state["adaptive_phase_tree"] = [n.model_dump() for n in nodes]


def get_active_tree_node(state: LaunchGraphState) -> AdaptiveTreeNode | None:
    active_id = str(state.get("adaptive_active_node_id") or "").strip()
    if not active_id:
        return None
    for node in get_adaptive_tree(state):
        if node.node_id == active_id and node.status == "active":
            return node
    return None


def get_active_spec(state: LaunchGraphState) -> PhaseSpec | None:
    node = get_active_tree_node(state)
    return node.spec if node is not None else None


def is_phase_node_completed(state: LaunchGraphState, node_id: str) -> bool:
    completed = state.get("completed_nodes") or {}
    bucket = completed.get(node_id)
    if bucket and bucket.get("done"):
        return True
    for node in get_adaptive_tree(state):
        if node.node_id == node_id and node.status == "done":
            return True
    return False


def completed_phases_summary(state: LaunchGraphState) -> str:
    lines: list[str] = []
    for node in get_adaptive_tree(state):
        label = node.phase_label or node.phase_id or "?"
        if node.status == "done" or is_phase_node_completed(state, node.node_id):
            lines.append(f"- {label} ({node.node_id}) [done]")
        elif node.status == "active":
            lines.append(f"- {label} ({node.node_id}) [active]")
        elif node.status == "failed":
            lines.append(f"- {label} ({node.node_id}) [failed]")
    completed = state.get("completed_nodes") or {}
    for key, value in completed.items():
        if not str(key).startswith("adaptive."):
            continue
        if not value.get("done"):
            continue
        if any(n.node_id == key for n in get_adaptive_tree(state)):
            continue
        lines.append(f"- {key} [done]")
    return "\n".join(lines[-12:])


def adaptive_tree_trace(state: LaunchGraphState) -> str:
    parts = ["post_login.adaptive"]
    for node in get_adaptive_tree(state):
        parts.append(f"{node.node_id}[{node.status}]")
    return " → ".join(parts)


def sync_phase_registry(state: LaunchGraphState) -> None:
    registry: list[dict[str, Any]] = []
    for node in get_adaptive_tree(state):
        registry.append(
            PhaseRecord(
                node_id=node.node_id,
                phase_id=node.phase_id,
                phase_label=node.phase_label,
                done=node.status == "done",
                attempts=node.attempts,
                artifact=node.artifact,
                evidence=node.evidence,
            ).model_dump()
        )
    state["phase_registry"] = registry


def decide_phase_spec(state: LaunchGraphState, spec: PhaseSpec, *, min_confidence: float) -> DecideOutcome:
    if not spec.flow_active or spec.phase_id == "skip":
        return DecideOutcome(kind=DecideOutcomeKind.FLOW_DONE, spec=spec, reason=spec.reason)

    node_id = spec.node_id()
    if is_phase_node_completed(state, node_id):
        return DecideOutcome(
            kind=DecideOutcomeKind.REJECT_DUPLICATE,
            spec=spec,
            reason=f"phase already done: {node_id}",
        )

    if spec.action not in ("tap_xy", "wait", "press_back", "dismiss_blank", "none"):
        downgraded = spec.model_copy(update={"action": "none", "confidence": 0.0})
        return DecideOutcome(
            kind=DecideOutcomeKind.ACCEPT,
            spec=downgraded,
            reason="invalid action → none",
        )

    if spec.confidence < min_confidence and spec.action in ("tap_xy", "press_back"):
        downgraded = spec.model_copy(
            update={
                "action": "wait",
                "wait_s": 2.0,
                "reason": (spec.reason or "")[:400] + " [low confidence → wait]",
            },
        )
        return DecideOutcome(kind=DecideOutcomeKind.ACCEPT, spec=downgraded)

    return DecideOutcome(kind=DecideOutcomeKind.ACCEPT, spec=spec)


def materialize_tree_node(
    state: LaunchGraphState,
    spec: PhaseSpec,
    *,
    entry_fingerprint: str,
    created_round: int,
) -> AdaptiveTreeNode:
    node = AdaptiveTreeNode(
        node_id=spec.node_id(),
        phase_id=spec.phase_id,
        phase_label=spec.phase_label,
        spec=spec,
        status="active",
        entry_fingerprint=entry_fingerprint,
        attempts=0,
        created_round=created_round,
    )
    tree = get_adaptive_tree(state)
    tree.append(node)
    _set_adaptive_tree(state, tree)
    state["adaptive_active_node_id"] = node.node_id
    state["phase_entry_fingerprint"] = entry_fingerprint
    state["current_phase_spec"] = spec.model_dump()
    sync_phase_registry(state)
    return node


def update_active_tree_node(state: LaunchGraphState, node: AdaptiveTreeNode) -> None:
    tree = get_adaptive_tree(state)
    updated: list[AdaptiveTreeNode] = []
    for item in tree:
        if item.node_id == node.node_id:
            updated.append(node)
        else:
            updated.append(item)
    _set_adaptive_tree(state, updated)
    sync_phase_registry(state)


def commit_tree_node(
    state: LaunchGraphState,
    node: AdaptiveTreeNode,
    *,
    artifact: str,
    evidence: str,
) -> None:
    mark_node_done(state, node.node_id, artifact=artifact, evidence=evidence[:500])
    committed = node.model_copy(
        update={
            "status": "done",
            "attempts": node.attempts + 1,
            "artifact": artifact,
            "evidence": evidence[:500],
        },
    )
    update_active_tree_node(state, committed)
    state["adaptive_active_node_id"] = ""
    state.pop("current_phase_spec", None)
    state["phase_entry_fingerprint"] = ""
    state["adaptive_no_progress"] = 0
    sync_phase_registry(state)


def fail_active_tree_node(state: LaunchGraphState, *, reason: str) -> None:
    node = get_active_tree_node(state)
    if node is None:
        return
    failed = node.model_copy(
        update={
            "status": "failed",
            "attempts": node.attempts + 1,
            "evidence": reason[:500],
        },
    )
    update_active_tree_node(state, failed)
    state["adaptive_active_node_id"] = ""
    state.pop("current_phase_spec", None)
    state["phase_entry_fingerprint"] = ""
    sync_phase_registry(state)


def increment_active_attempts(state: LaunchGraphState) -> AdaptiveTreeNode | None:
    node = get_active_tree_node(state)
    if node is None:
        return None
    bumped = node.model_copy(update={"attempts": node.attempts + 1})
    update_active_tree_node(state, bumped)
    return bumped


def mark_adaptive_flow_done(state: LaunchGraphState, *, evidence: str = "") -> None:
    state["adaptive_flow_done"] = True
    state["adaptive_active_node_id"] = ""
    state.pop("current_phase_spec", None)
    state["phase_entry_fingerprint"] = ""
    if evidence:
        state["recover_hint"] = evidence[:500]


def adaptive_parent_attempts(state: LaunchGraphState) -> int:
    """post_login.adaptive 物理入口尝试次数（与子节点分离）。"""
    key = "post_login.adaptive"
    for bucket in (state.get("failed_nodes") or {}, state.get("completed_nodes") or {}):
        if key in bucket:
            return int(bucket[key].get("attempts", 0))
    return 0


def mark_adaptive_parent_done(state: LaunchGraphState, *, evidence: str = "") -> None:
    from game_agent.graphs.launch_state_store import mark_tree_node_done

    mark_tree_node_done(state, "adaptive_phase", evidence=evidence[:500])


def mark_adaptive_parent_failed(state: LaunchGraphState, error: str) -> None:
    from game_agent.graphs.launch_state_store import mark_tree_node_failed

    mark_tree_node_failed(state, "adaptive_phase", error)
    mark_adaptive_flow_done(state, evidence=error[:500])
