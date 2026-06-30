"""局内行为链状态管理（执行由 behavior_chain + 主脑规划驱动）。"""

from __future__ import annotations

import logging
import re
from typing import Any

from game_agent.services.behavior_chain import (
    BehaviorChain,
    BehaviorStep,
    can_replan_behavior_chain,
    record_behavior_chain_failure,
)
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)

_TAP_TEXT_RE = re.compile(
    r"^(确定|确认|继续|关闭|取消|领取|前往|挑战|开始|OK|Continue|Close|Skip)$",
    re.IGNORECASE,
)

_IN_GAME_CHAIN_PREFIX = "in_game_behavior"


def get_in_game_behavior_chain(state: dict[str, Any]) -> BehaviorChain | None:
    raw = state.get("in_game_behavior_chain")
    if not raw:
        return None
    try:
        return BehaviorChain.model_validate({"steps": raw})
    except Exception:
        return None


def get_current_in_game_behavior_step(state: dict[str, Any]) -> BehaviorStep | None:
    chain = get_in_game_behavior_chain(state)
    if chain is None:
        return None
    cursor = int(state.get("in_game_behavior_cursor") or 0)
    if cursor < 0 or cursor >= len(chain.steps):
        return None
    return chain.steps[cursor]


def set_in_game_behavior_chain(state: dict[str, Any], chain: BehaviorChain) -> None:
    state["in_game_behavior_chain"] = [step.model_dump() for step in chain.steps]
    state["in_game_behavior_cursor"] = 0
    state["in_game_behavior_no_progress"] = 0
    state["in_game_behavior_last_fingerprint"] = ""
    logger.info(
        "[InGameBehavior] chain_built source=%s goal=%s steps=%d",
        chain.source,
        chain.goal[:80],
        len(chain.steps),
    )


def clear_in_game_behavior_chain(
    state: dict[str, Any],
    *,
    completed: bool = False,
) -> None:
    state["in_game_behavior_chain"] = []
    state["in_game_behavior_cursor"] = 0
    state["in_game_behavior_no_progress"] = 0
    state["in_game_behavior_last_fingerprint"] = ""
    if completed:
        state["in_game_behavior_failure_trace"] = []
        state["in_game_behavior_replan_count"] = 0
        state["in_game_behavior_last_failed_step_id"] = ""


def advance_in_game_behavior_cursor(state: dict[str, Any]) -> bool:
    chain = get_in_game_behavior_chain(state)
    if chain is None:
        return False
    cursor = int(state.get("in_game_behavior_cursor") or 0) + 1
    state["in_game_behavior_cursor"] = cursor
    if cursor >= len(chain.steps):
        clear_in_game_behavior_chain(state, completed=True)
        return False
    return True


def mark_in_game_behavior_attempt(
    state: dict[str, Any],
    step: BehaviorStep,
    *,
    done: bool,
) -> BehaviorStep:
    chain = get_in_game_behavior_chain(state)
    if chain is None:
        return step
    cursor = int(state.get("in_game_behavior_cursor") or 0)
    if cursor < 0 or cursor >= len(chain.steps):
        return step
    cur = chain.steps[cursor]
    cur.attempts += 1
    if done:
        cur.done = True
    chain.steps[cursor] = cur
    state["in_game_behavior_chain"] = [item.model_dump() for item in chain.steps]
    return cur


def fallback_in_game_behavior_chain(bboxes: list[OcrBbox]) -> BehaviorChain | None:
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if text and _TAP_TEXT_RE.match(text):
            return BehaviorChain(
                source="heuristic_fallback",
                stage="in_game",
                goal="resolve visible blocking button",
                steps=[
                    BehaviorStep(
                        id="tap_visible_button",
                        action="tap_xy",
                        x=bbox.cx,
                        y=bbox.cy,
                        target_text=text,
                        intent="tap visible confirmation/action button",
                        success_criteria=["button disappears", "screen changes"],
                        reason="fallback visible button",
                    ),
                    BehaviorStep(
                        id="observe_after_button",
                        action="wait",
                        wait_s=1.5,
                        intent="wait for UI transition after tap",
                        success_criteria=["new UI state is visible"],
                        reason="allow transition",
                    ),
                ],
            )
    return None


def record_in_game_behavior_failure(
    state: dict[str, Any],
    step: BehaviorStep,
    *,
    reason: str,
    ocr_summary: str = "",
    artifact: str = "",
):
    return record_behavior_chain_failure(
        state,
        step,
        prefix=_IN_GAME_CHAIN_PREFIX,
        reason=reason,
        ocr_summary=ocr_summary,
        artifact=artifact,
    )
