"""ActionFrame 纠正：根据检讨结果修补 facts / 坐标，不绕过里程碑。"""

from __future__ import annotations

from typing import Any

from game_agent.models.launch_graph_state import LaunchFacts, LaunchGraphState
from game_agent.services.action_reflection import ActionReflection


def apply_fact_patches_to_state(
    state: LaunchGraphState,
    patches: dict[str, Any],
) -> LaunchFacts:
    """将 fact_patches 合并进 state['facts'] 并返回 LaunchFacts。"""
    if not patches:
        from game_agent.models.launch_graph_state import facts_from_state

        return facts_from_state(state)

    from game_agent.models.launch_graph_state import facts_from_state

    facts = facts_from_state(state)
    merged = facts.model_copy(update=patches)
    state["facts"] = merged.model_dump()
    return merged


def apply_correction(
    state: LaunchGraphState,
    reflection: ActionReflection,
) -> tuple[LaunchFacts, dict[str, Any]]:
    """
    应用纠正结果到 state。
    返回 (更新后 facts, correction_meta)。
    """
    meta: dict[str, Any] = {
        "root_cause": reflection.root_cause,
        "recover_hint": reflection.recover_hint,
    }
    facts = apply_fact_patches_to_state(state, reflection.fact_patches)
    if reflection.retry_coords is not None:
        x, y = reflection.retry_coords
        meta["retry_coords"] = (x, y)
        if reflection.root_cause == "wrong_coords":
            facts = facts.model_copy(update={"agree_button_xy": (x, y)})
            state["facts"] = facts.model_dump()
    if reflection.recover_hint:
        state["recover_hint"] = reflection.recover_hint[:300]
    if reflection.root_cause == "wrong_route":
        state["last_reflection"] = reflection.model_dump()
    return facts, meta
