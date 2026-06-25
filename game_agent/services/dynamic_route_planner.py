"""动态子树链：登录后按画面生成有序动作链（attempt 内有效）。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from game_agent.models.launch_graph_state import LaunchFacts, LaunchGraphState
from game_agent.services.behavior_chain import (
    BehaviorChain,
    BehaviorFailureTrace,
    BehaviorStep,
    behavior_failure_trace,
    behavior_progress_fingerprint,
    can_replan_behavior_chain,
    parse_behavior_chain_json,
    record_behavior_chain_failure,
    validate_behavior_chain,
)
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)

DynamicActionType = Literal["tap_xy", "tap_text", "swipe", "wait", "press_back", "none"]

_ENTER_WORLD_RE = re.compile(
    r"进入世界|Enter\s*World|进入游戏|开始游戏",
    re.IGNORECASE,
)
_CREATE_ROLE_RE = re.compile(
    r"创建角色|Click\s*to\s*Create|Create\s*Role|新建角色",
    re.IGNORECASE,
)
_CHAR_SLOT_RE = re.compile(r"LV\.|等级|Lv\.|角色", re.IGNORECASE)
_BEHAVIOR_CHAIN_HINT_RE = re.compile(
    r"创角|创建角色|Enter\s*World|进入世界|进入游戏|开始游戏|Click\s*to\s*Create|LV\.|等级|角色",
    re.IGNORECASE,
)


class DynamicActionStep(BehaviorStep):
    id: str


class DynamicActionChain(BehaviorChain):
    steps: list[DynamicActionStep] = Field(default_factory=list)


class DynamicFailureTrace(BehaviorFailureTrace):
    pass


def _bbox_for_pattern(bboxes: list[OcrBbox], pattern: re.Pattern[str]) -> OcrBbox | None:
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text or not pattern.search(text):
            continue
        candidates.append((bbox.cy, bbox))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _find_character_slot_bbox(bboxes: list[OcrBbox]) -> OcrBbox | None:
    """已有角色槽（含 LV. 等），排除创建角色占位。"""
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text or _CREATE_ROLE_RE.search(text):
            continue
        if _CHAR_SLOT_RE.search(text):
            candidates.append((bbox.cy, bbox))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def build_dynamic_chain_heuristic(
    bboxes: list[OcrBbox],
    *,
    ocr_summary: str,
    facts: LaunchFacts,
    replan_from_step_id: str = "",
    failure_context: list[dict[str, Any]] | None = None,
) -> DynamicActionChain | None:
    """启发式：选角 -> 进入世界；或仅创建角色/进入世界。"""
    merged = ocr_summary or " ".join(b.text for b in bboxes)
    enter = _bbox_for_pattern(bboxes, _ENTER_WORLD_RE)
    char_slot = _find_character_slot_bbox(bboxes)
    create = _bbox_for_pattern(bboxes, _CREATE_ROLE_RE)

    steps: list[DynamicActionStep] = []

    if char_slot and enter and not replan_from_step_id:
        steps.append(
            DynamicActionStep(
                id="select_character",
                action="tap_xy",
                x=char_slot.cx,
                y=char_slot.cy,
                target_text=char_slot.text.strip(),
                label="select_character",
                success_criteria=["角色槽被选中", "开始游戏/进入世界按钮仍可见"],
                reason="select existing character slot before enter",
            ),
        )
        steps.append(
            DynamicActionStep(
                id="enter_world",
                action="tap_xy",
                x=enter.cx,
                y=enter.cy,
                target_text=enter.text.strip(),
                label="enter_world",
                success_criteria=["进入 loading", "进入 in_game_hud", "进入按钮消失"],
                reason="tap enter world after character selected",
            ),
        )
    elif replan_from_step_id == "select_character" and enter:
        steps.append(
            DynamicActionStep(
                id="enter_world",
                action="tap_xy",
                x=enter.cx,
                y=enter.cy,
                target_text=enter.text.strip(),
                label="enter_world",
                success_criteria=["进入 loading", "进入 in_game_hud", "进入按钮消失"],
                reason="replan after select_character stalled; tap visible enter CTA",
            ),
        )
    elif replan_from_step_id == "enter_world" and enter:
        steps.append(
            DynamicActionStep(
                id="wait_after_enter_retry",
                action="wait",
                wait_s=1.5,
                label="wait_after_enter_retry",
                success_criteria=["界面刷新或进入 loading"],
                reason="replan from failed enter_world: wait before retry",
            ),
        )
        steps.append(
            DynamicActionStep(
                id="enter_world_retry",
                action="tap_xy",
                x=enter.cx,
                y=enter.cy,
                target_text=enter.text.strip(),
                label="enter_world",
                success_criteria=["进入 loading", "进入 in_game_hud", "进入按钮消失"],
                reason="retry visible enter CTA after wait",
            ),
        )
    elif enter and (
        facts.character_creation_blocking
        or _CHAR_SLOT_RE.search(merged)
    ):
        steps.append(
            DynamicActionStep(
                id="enter_world",
                action="tap_xy",
                x=enter.cx,
                y=enter.cy,
                target_text=enter.text.strip(),
                label="enter_world",
                success_criteria=["进入 loading", "进入 in_game_hud", "进入按钮消失"],
                reason="tap enter world on character select screen",
            ),
        )
    elif enter:
        steps.append(
            DynamicActionStep(
                id="enter_world",
                action="tap_xy",
                x=enter.cx,
                y=enter.cy,
                target_text=enter.text.strip(),
                label="enter_world",
                success_criteria=["进入 loading", "进入 in_game_hud", "进入按钮消失"],
                reason="tap enter world CTA",
            ),
        )
    elif create:
        steps.append(
            DynamicActionStep(
                id="create_role",
                action="tap_xy",
                x=create.cx,
                y=create.cy,
                target_text=create.text.strip(),
                label="create_role",
                success_criteria=["进入创角流程", "出现职业/角色创建控件"],
                reason="tap create role entry",
            ),
        )
    elif facts.character_creation_blocking or facts.interpreter_stage == "character_creation":
        return None

    if not steps:
        return None

    return DynamicActionChain(
        steps=steps,
        source="heuristic_replan" if replan_from_step_id else "heuristic",
        stage="character_select" if char_slot else "character_creation",
        replan_from_step_id=replan_from_step_id,
        failure_context=failure_context or [],
    )


def parse_dynamic_chain_vision(raw: str) -> DynamicActionChain | None:
    chain = parse_behavior_chain_json(raw, max_wait_s=5.0, max_steps=7)
    if chain is None:
        return None
    return DynamicActionChain.model_validate(chain.model_dump())


def validate_chain(chain: DynamicActionChain) -> DynamicActionChain | None:
    validated = validate_behavior_chain(chain)
    if validated is None:
        return None
    return DynamicActionChain.model_validate(validated.model_dump())


def should_build_dynamic_chain(
    state: LaunchGraphState,
    facts: LaunchFacts,
    *,
    ocr_summary: str = "",
) -> bool:
    if not state.get("login_done"):
        return False
    if state.get("in_game_confirmed"):
        return False
    if state.get("in_game_entry_passed"):
        return False
    if state.get("current_phase_spec") or state.get("adaptive_active_node_id"):
        return False
    has_chain_hint = bool(
        facts.character_creation_blocking
        or facts.interpreter_stage == "character_creation"
        or facts.vision_stage == "character_creation"
        or _BEHAVIOR_CHAIN_HINT_RE.search(ocr_summary or "")
    )
    if not state.get("adaptive_flow_done") and state.get("login_done") and not has_chain_hint:
        from game_agent.graphs.launch_routing import should_route_adaptive
        from game_agent.models.launch_graph_state import facts_from_state

        if should_route_adaptive(state, facts_from_state(state)):
            return False
    if state.get("dynamic_failed"):
        return False
    if get_dynamic_chain(state) is not None:
        return False
    if facts.login_blocking or facts.sub_account_blocking:
        return False
    if facts.initial_privacy_dialog:
        return False
    if facts.download_visible:
        return False
    if not has_chain_hint:
        return False
    if facts.character_creation_blocking:
        return True
    if facts.interpreter_stage == "character_creation":
        return True
    if facts.vision_stage == "character_creation":
        return True
    if _BEHAVIOR_CHAIN_HINT_RE.search(ocr_summary or ""):
        return True
    return False


def set_dynamic_chain(state: LaunchGraphState, chain: DynamicActionChain) -> None:
    state["dynamic_chain"] = chain.model_dump()
    state["dynamic_cursor"] = 0
    state["dynamic_rounds"] = 0
    state["dynamic_no_progress"] = 0
    state["dynamic_last_fingerprint"] = ""
    state["dynamic_failed"] = False
    logger.info(
        "[DynamicChain] built source=%s steps=%d labels=%s",
        chain.source,
        len(chain.steps),
        [s.label or s.id for s in chain.steps],
    )


def clear_dynamic_chain(
    state: LaunchGraphState,
    *,
    failed: bool = False,
    completed: bool = False,
) -> None:
    state["dynamic_chain"] = []
    state["dynamic_cursor"] = 0
    if completed:
        state["dynamic_failure_trace"] = []
        state["dynamic_replan_count"] = 0
        state["dynamic_last_failed_step_id"] = ""
    if failed:
        state["dynamic_failed"] = True


def dynamic_failure_trace(state: LaunchGraphState) -> list[dict[str, Any]]:
    return behavior_failure_trace(state, prefix="dynamic")


def can_replan_dynamic_chain(state: LaunchGraphState, *, max_replans: int) -> bool:
    return can_replan_behavior_chain(state, prefix="dynamic", max_replans=max_replans)


def record_dynamic_chain_failure(
    state: LaunchGraphState,
    step: DynamicActionStep,
    *,
    reason: str,
    ocr_summary: str = "",
    artifact: str = "",
) -> DynamicFailureTrace:
    trace = record_behavior_chain_failure(
        state,
        step,
        prefix="dynamic",
        reason=reason,
        ocr_summary=ocr_summary,
        artifact=artifact,
    )
    state["dynamic_failed"] = False
    state["recover_hint"] = f"dynamic_replan:{step.id}:{reason[:160]}"
    return DynamicFailureTrace.model_validate(trace.model_dump())


def get_dynamic_chain(state: LaunchGraphState) -> DynamicActionChain | None:
    raw = state.get("dynamic_chain")
    if not raw:
        return None
    try:
        chain = DynamicActionChain.model_validate(raw)
    except Exception:
        return None
    if not chain.steps:
        return None
    cursor = int(state.get("dynamic_cursor") or 0)
    if cursor >= len(chain.steps):
        return None
    return chain


def get_current_step(state: LaunchGraphState) -> DynamicActionStep | None:
    chain = get_dynamic_chain(state)
    if chain is None:
        return None
    cursor = int(state.get("dynamic_cursor") or 0)
    if cursor < 0 or cursor >= len(chain.steps):
        return None
    return chain.steps[cursor]


def advance_dynamic_cursor(state: LaunchGraphState) -> bool:
    """前进一步；链执行完毕返回 False。"""
    chain = get_dynamic_chain(state)
    if chain is None:
        return False
    cursor = int(state.get("dynamic_cursor") or 0) + 1
    state["dynamic_cursor"] = cursor
    if cursor >= len(chain.steps):
        clear_dynamic_chain(state, completed=True)
        logger.info("[DynamicChain] completed all steps")
        return False
    state["dynamic_chain"] = chain.model_dump()
    return True


def mark_step_attempt(state: LaunchGraphState, step: DynamicActionStep, *, done: bool) -> None:
    chain = get_dynamic_chain(state)
    if chain is None:
        return
    cursor = int(state.get("dynamic_cursor") or 0)
    if cursor >= len(chain.steps):
        return
    cur = chain.steps[cursor]
    cur.attempts += 1
    if done:
        cur.done = True
    chain.steps[cursor] = cur
    state["dynamic_chain"] = chain.model_dump()


def chain_progress_fingerprint(
    *,
    ocr_summary: str,
    stage: str = "",
) -> str:
    return behavior_progress_fingerprint(ocr_summary=ocr_summary, stage=stage)


def has_active_dynamic_chain(state: LaunchGraphState) -> bool:
    return get_current_step(state) is not None and not state.get("dynamic_failed")


def maybe_build_dynamic_chain(
    state: LaunchGraphState,
    facts: LaunchFacts,
    bboxes: list[OcrBbox],
    *,
    ocr_summary: str,
) -> bool:
    if not should_build_dynamic_chain(state, facts, ocr_summary=ocr_summary):
        return False
    failed_step_id = str(state.get("dynamic_last_failed_step_id") or "")
    traces = dynamic_failure_trace(state)
    chain = build_dynamic_chain_heuristic(
        bboxes,
        ocr_summary=ocr_summary,
        facts=facts,
        replan_from_step_id=failed_step_id,
        failure_context=traces,
    )
    if chain is None:
        return False
    validated = validate_chain(chain)
    if validated is None:
        return False
    set_dynamic_chain(state, validated)
    return True


def chain_to_audit_dict(chain: DynamicActionChain) -> dict[str, Any]:
    return chain.model_dump()
