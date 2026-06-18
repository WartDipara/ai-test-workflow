"""动态子树链：登录后按画面生成有序动作链（attempt 内有效）。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from game_agent.models.launch_graph_state import LaunchFacts, LaunchGraphState
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)

DynamicActionType = Literal["tap_xy", "wait", "press_back"]

_ENTER_WORLD_RE = re.compile(
    r"进入世界|Enter\s*World|进入游戏|开始游戏",
    re.IGNORECASE,
)
_CREATE_ROLE_RE = re.compile(
    r"创建角色|Click\s*to\s*Create|Create\s*Role|新建角色",
    re.IGNORECASE,
)
_CHAR_SLOT_RE = re.compile(r"LV\.|等级|Lv\.|角色", re.IGNORECASE)


class DynamicActionStep(BaseModel):
    id: str
    action: DynamicActionType = "tap_xy"
    x: int = 0
    y: int = 0
    target_text: str = ""
    wait_s: float = 1.5
    reason: str = ""
    label: str = ""
    max_attempts: int = 2
    attempts: int = 0
    done: bool = False

    def signature(self) -> str:
        return f"{self.action}:{self.x}:{self.y}:{self.target_text}:{self.wait_s:.1f}"


class DynamicActionChain(BaseModel):
    steps: list[DynamicActionStep] = Field(default_factory=list)
    source: str = ""
    stage: str = ""


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
) -> DynamicActionChain | None:
    """启发式：选角 -> 进入世界；或仅创建角色/进入世界。"""
    merged = ocr_summary or " ".join(b.text for b in bboxes)
    enter = _bbox_for_pattern(bboxes, _ENTER_WORLD_RE)
    char_slot = _find_character_slot_bbox(bboxes)
    create = _bbox_for_pattern(bboxes, _CREATE_ROLE_RE)

    steps: list[DynamicActionStep] = []

    if char_slot and enter:
        steps.append(
            DynamicActionStep(
                id="select_character",
                action="tap_xy",
                x=char_slot.cx,
                y=char_slot.cy,
                target_text=char_slot.text.strip(),
                label="select_character",
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
                reason="tap enter world after character selected",
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
                reason="tap create role entry",
            ),
        )
    elif facts.character_creation_blocking or facts.interpreter_stage == "character_creation":
        return None

    if not steps:
        return None

    return DynamicActionChain(
        steps=steps,
        source="heuristic",
        stage="character_select" if char_slot else "character_creation",
    )


def parse_dynamic_chain_vision(raw: str) -> DynamicActionChain | None:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw_steps = data.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        return None
    steps: list[DynamicActionStep] = []
    for i, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "tap_xy") or "tap_xy").strip().lower()
        if action not in ("tap_xy", "wait", "press_back"):
            continue
        try:
            x = int(item.get("x", 0) or 0)
            y = int(item.get("y", 0) or 0)
            wait_s = float(item.get("wait_s", 1.5) or 1.5)
        except (TypeError, ValueError):
            x, y, wait_s = 0, 0, 1.5
        steps.append(
            DynamicActionStep(
                id=str(item.get("id", f"step_{i}") or f"step_{i}"),
                action=action,  # type: ignore[arg-type]
                x=x,
                y=y,
                target_text=str(item.get("target_text", "") or ""),
                wait_s=max(0.5, min(wait_s, 5.0)),
                reason=str(item.get("reason", "") or ""),
                label=str(item.get("label", "") or ""),
            ),
        )
    if not steps:
        return None
    return DynamicActionChain(
        steps=steps,
        source="vision",
        stage=str(data.get("stage", "") or ""),
    )


def validate_chain(chain: DynamicActionChain) -> DynamicActionChain | None:
    if not chain.steps:
        return None
    for step in chain.steps:
        if step.action == "tap_xy" and (step.x <= 0 or step.y <= 0):
            return None
    return chain


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
    if not state.get("adaptive_flow_done") and state.get("login_done"):
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
    if facts.character_creation_blocking:
        return True
    if facts.interpreter_stage == "character_creation":
        return True
    if facts.vision_stage == "character_creation":
        return True
    merged = ocr_summary or ""
    if re.search(
        r"创角|创建角色|Enter\s*World|进入世界|Click\s*to\s*Create|LV\.",
        merged,
        re.IGNORECASE,
    ):
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


def clear_dynamic_chain(state: LaunchGraphState, *, failed: bool = False) -> None:
    state["dynamic_chain"] = []
    state["dynamic_cursor"] = 0
    if failed:
        state["dynamic_failed"] = True


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
        clear_dynamic_chain(state)
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
    return f"{stage}|{(ocr_summary or '')[:300]}"


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
    chain = build_dynamic_chain_heuristic(bboxes, ocr_summary=ocr_summary, facts=facts)
    if chain is None:
        return False
    validated = validate_chain(chain)
    if validated is None:
        return False
    set_dynamic_chain(state, validated)
    return True


def chain_to_audit_dict(chain: DynamicActionChain) -> dict[str, Any]:
    return chain.model_dump()
