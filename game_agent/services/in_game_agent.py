"""进入游戏后 LLM 驱动的开放式动作规划与执行（白名单内）。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Literal

from game_agent.services.behavior_chain import (
    BehaviorChain,
    BehaviorStep,
    behavior_failure_trace,
    behavior_progress_fingerprint,
    can_replan_behavior_chain,
    execute_behavior_step,
    parse_behavior_chain_json,
    record_behavior_chain_failure,
    validate_behavior_chain,
)
from game_agent.utils.ocr_util import OcrBbox
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)

InGameActionType = Literal["tap_xy", "tap_text", "swipe", "press_back", "wait", "none"]

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
        chain = BehaviorChain.model_validate({"steps": raw})
    except Exception:
        return None
    cursor = int(state.get("in_game_behavior_cursor") or 0)
    if cursor < 0 or cursor >= len(chain.steps):
        return None
    return chain


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


async def decide_in_game_behavior_chain(
    *,
    vision: VisionWorker | None,
    screenshot_path: Path,
    bboxes: list[OcrBbox],
    ocr_summary: str,
    round_id: int,
    remaining_s: float,
    external_log_summary: str = "",
    failure_context: list[dict[str, Any]] | None = None,
    replan_from_step_id: str = "",
    screen_w: int,
    screen_h: int,
    max_action_wait_s: float = 5.0,
) -> BehaviorChain | None:
    if vision is None:
        return _fallback_in_game_behavior_chain(bboxes)

    failure_hint = ""
    if failure_context:
        failure_hint = f"\nRecent failed chain steps (avoid repeating them):\n{failure_context[-3:]}\n"
    if replan_from_step_id:
        failure_hint += f"\nReplan from failed step id: {replan_from_step_id}\n"
    log_hint = ""
    if external_log_summary.strip():
        log_hint = f"\nRecent external log excerpt:\n{external_log_summary[:1200]}\n"

    prompt = f"""
You are controlling an Android game that is already past login. Build a SHORT behavior chain, not a single tap.
The chain must work for any game situation: combat, rewards, dialogs, tutorials, map movement, inventory, or idle HUD.

Remaining run time: {remaining_s:.0f}s

OCR (x,y text):
{ocr_summary}
{log_hint}
{failure_hint}

Plan 3-7 safe steps with general intents. Do not hard-code a workflow name.
Use only these actions:
- tap_xy: tap a visible UI coordinate
- tap_text: tap OCR text in target_text
- swipe: drag/move from (x,y) to (x2,y2)
- press_back: dismiss a blocking overlay
- wait: wait for animation/combat/loading
- none: no safe action

Forbidden: typing credentials, install/uninstall, Android settings, adb/shell/system commands.

Each step MUST include:
- intent: what this step is trying to accomplish
- success_criteria: generic visible outcome(s) to check after execution

JSON only:
{{
  "source": "vision",
  "stage": "in_game",
  "goal": "short goal for the next few steps",
  "replan_from_step_id": "{replan_from_step_id}",
  "steps": [
    {{
      "id": "step_1",
      "action": "tap_xy | tap_text | swipe | press_back | wait | none",
      "x": 0,
      "y": 0,
      "x2": 0,
      "y2": 0,
      "target_text": "",
      "wait_s": 1.5,
      "intent": "one specific intent",
      "success_criteria": ["visible outcome"],
      "reason": "one sentence"
    }}
  ]
}}
"""
    raw = await vision.plan_free_step(
        screenshot_path=screenshot_path,
        prompt=prompt,
        round_id=round_id,
    )
    chain = parse_behavior_chain_json(
        raw,
        screen_w=screen_w,
        screen_h=screen_h,
        max_wait_s=max_action_wait_s,
        max_steps=7,
    )
    if chain is None:
        logger.warning("[InGameBehavior] chain JSON parse failed: %s", raw[:200])
        return _fallback_in_game_behavior_chain(bboxes)
    return validate_behavior_chain(
        chain,
        bboxes=bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
    )


def _fallback_in_game_behavior_chain(bboxes: list[OcrBbox]) -> BehaviorChain | None:
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


def can_replan_in_game_behavior_chain(state: dict[str, Any], *, max_replans: int) -> bool:
    return can_replan_behavior_chain(
        state,
        prefix=_IN_GAME_CHAIN_PREFIX,
        max_replans=max_replans,
    )


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


@dataclass(frozen=True, slots=True)
class InGameActionPlan:
    action: InGameActionType
    x: int = 0
    y: int = 0
    x2: int = 0
    y2: int = 0
    target_text: str = ""
    wait_s: float = 2.0
    reason: str = ""
    stage: str = "in_game"

    def signature(self) -> str:
        return (
            f"{self.action}:{self.x}:{self.y}:{self.x2}:{self.y2}:"
            f"{self.target_text}:{self.wait_s:.1f}"
        )


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```json"):
        s = s[7:]
    if s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def _clamp_coord(value: int, limit: int) -> int:
    if limit <= 0:
        return max(0, value)
    return max(0, min(value, limit))


def _bbox_for_text(bboxes: list[OcrBbox], target: str) -> OcrBbox | None:
    needle = (target or "").strip()
    if not needle:
        return None
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text:
            continue
        if needle in text or text in needle:
            return bbox
    return None


def _parse_in_game_action_json(
    raw: str,
    *,
    screen_w: int,
    screen_h: int,
    max_wait_s: float,
) -> InGameActionPlan | None:
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action", "") or "none").strip().lower()
    if action not in ("tap_text", "tap_xy", "swipe", "press_back", "wait", "none"):
        action = "none"
    try:
        x = _clamp_coord(int(data.get("x", 0) or 0), screen_w)
        y = _clamp_coord(int(data.get("y", 0) or 0), screen_h)
        x2 = _clamp_coord(int(data.get("x2", 0) or 0), screen_w)
        y2 = _clamp_coord(int(data.get("y2", 0) or 0), screen_h)
    except (TypeError, ValueError):
        x, y, x2, y2 = 0, 0, 0, 0
    try:
        wait_s = float(data.get("wait_s", 2.0) or 2.0)
    except (TypeError, ValueError):
        wait_s = 2.0
    wait_s = max(0.5, min(wait_s, max_wait_s))
    return InGameActionPlan(
        action=action,  # type: ignore[arg-type]
        x=x,
        y=y,
        x2=x2,
        y2=y2,
        target_text=str(data.get("target_text", "") or "")[:80],
        wait_s=wait_s,
        reason=str(data.get("reason", "") or "")[:300],
        stage=str(data.get("stage", "") or "in_game")[:60],
    )


def resolve_tap_text_coords(
    plan: InGameActionPlan,
    bboxes: list[OcrBbox],
) -> InGameActionPlan:
    if plan.action != "tap_text":
        return plan
    target = plan.target_text.strip()
    if not target:
        return InGameActionPlan(
            action="none",
            reason="tap_text missing target_text",
            stage=plan.stage,
        )
    bbox = _bbox_for_text(bboxes, target)
    if bbox is None:
        return InGameActionPlan(
            action="wait",
            wait_s=plan.wait_s,
            reason=f"tap_text not found: {target[:40]}",
            stage=plan.stage,
        )
    return InGameActionPlan(
        action="tap_xy",
        x=bbox.cx,
        y=bbox.cy,
        target_text=target,
        wait_s=plan.wait_s,
        reason=plan.reason or f"tap_text:{target}",
        stage=plan.stage,
    )


def sanitize_in_game_plan(
    plan: InGameActionPlan,
    *,
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
    prior_signature: str = "",
    same_action_streak: int = 0,
    max_same_action: int = 2,
) -> InGameActionPlan:
    plan = resolve_tap_text_coords(plan, bboxes)
    sig = plan.signature()
    if (
        same_action_streak >= max_same_action
        and plan.action in ("tap_xy", "tap_text", "swipe")
        and sig == prior_signature
    ):
        return InGameActionPlan(
            action="wait",
            wait_s=plan.wait_s,
            reason=f"dedupe repeat {sig}",
            stage=plan.stage,
        )
    if plan.action == "tap_xy" and (plan.x <= 0 or plan.y <= 0):
        return InGameActionPlan(action="wait", wait_s=plan.wait_s, reason="invalid tap_xy")
    if plan.action == "swipe":
        if plan.x <= 0 or plan.y <= 0 or plan.x2 <= 0 or plan.y2 <= 0:
            return InGameActionPlan(action="wait", wait_s=plan.wait_s, reason="invalid swipe")
    if plan.action == "tap_text":
        return InGameActionPlan(action="wait", wait_s=plan.wait_s, reason="unresolved tap_text")
    return plan


async def decide_in_game_action_vision(
    vision: VisionWorker,
    *,
    screenshot_path: Path,
    ocr_summary: str,
    round_id: int,
    remaining_s: float,
    external_log_summary: str = "",
    prior_action_signature: str = "",
    screen_w: int,
    screen_h: int,
    max_action_wait_s: float,
) -> InGameActionPlan | None:
    avoid = ""
    if prior_action_signature:
        avoid = (
            f"\nPrevious action did not help: {prior_action_signature}. "
            "Pick a different safe action (wait, press_back, swipe, or another tap).\n"
        )
    log_hint = ""
    if external_log_summary.strip():
        log_hint = f"\nRecent external log excerpt:\n{external_log_summary[:1200]}\n"
    prompt = f"""
You automate an Android game that is ALREADY in the main in-game HUD (not login/download).
Explore and play naturally for about {remaining_s:.0f}s remaining in this session.

OCR (x,y text):
{ocr_summary}
{log_hint}
{avoid}

Pick exactly ONE action from this whitelist:
- tap_xy: tap visible UI at (x,y)
- tap_text: tap a button by OCR label in target_text
- swipe: scroll/map move from (x,y) to (x2,y2)
- press_back: dismiss blocking popup if safer than random tap
- wait: wait for animation/loading (wait_s 1.0-{min(5.0, remaining_s):.1f})
- none: cannot decide safely

FORBIDDEN: typing credentials, uninstall, install, system settings, adb/shell.

JSON only (no markdown):
{{
  "action": "tap_xy | tap_text | swipe | press_back | wait | none",
  "x": 0,
  "y": 0,
  "x2": 0,
  "y2": 0,
  "target_text": "",
  "wait_s": 2.0,
  "stage": "in_game",
  "reason": "one sentence"
}}
"""
    raw = await vision.plan_free_step(
        screenshot_path=screenshot_path,
        prompt=prompt,
        round_id=round_id,
    )
    plan = _parse_in_game_action_json(
        raw,
        screen_w=screen_w,
        screen_h=screen_h,
        max_wait_s=max_action_wait_s,
    )
    if plan is None:
        logger.warning("[InGameAgent] vision JSON parse failed: %s", raw[:200])
    return plan


async def decide_in_game_action(
    *,
    vision: VisionWorker | None,
    screenshot_path: Path,
    bboxes: list[OcrBbox],
    ocr_summary: str,
    round_id: int,
    remaining_s: float,
    external_log_summary: str = "",
    prior_action_signature: str = "",
    same_action_streak: int = 0,
    screen_w: int,
    screen_h: int,
    max_action_wait_s: float = 5.0,
    max_same_action: int = 2,
) -> InGameActionPlan:
    plan: InGameActionPlan | None = None
    if vision is not None:
        plan = await decide_in_game_action_vision(
            vision,
            screenshot_path=screenshot_path,
            ocr_summary=ocr_summary,
            round_id=round_id,
            remaining_s=remaining_s,
            external_log_summary=external_log_summary,
            prior_action_signature=prior_action_signature,
            screen_w=screen_w,
            screen_h=screen_h,
            max_action_wait_s=max_action_wait_s,
        )

    if plan is None:
        for bbox in bboxes:
            text = (bbox.text or "").strip()
            if text and _TAP_TEXT_RE.match(text):
                plan = InGameActionPlan(
                    action="tap_xy",
                    x=bbox.cx,
                    y=bbox.cy,
                    target_text=text,
                    reason="heuristic:dialog_button",
                )
                break

    if plan is None:
        plan = InGameActionPlan(action="wait", wait_s=2.0, reason="fallback_wait")

    return sanitize_in_game_plan(
        plan,
        bboxes=bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
        prior_signature=prior_action_signature,
        same_action_streak=same_action_streak,
        max_same_action=max_same_action,
    )


def execute_in_game_action(
    plan: InGameActionPlan,
    *,
    adb,
    sw: int,
    sh: int,
) -> str:
    if plan.action == "tap_xy":
        if plan.x <= 0 or plan.y <= 0:
            return f"refused tap invalid ({plan.x},{plan.y})"
        return adb.tap(plan.x, plan.y, width=sw, height=sh)
    if plan.action == "swipe":
        if plan.x <= 0 or plan.y <= 0 or plan.x2 <= 0 or plan.y2 <= 0:
            return "refused swipe invalid coords"
        return adb.swipe(plan.x, plan.y, plan.x2, plan.y2, width=sw, height=sh)
    if plan.action == "press_back":
        return adb.press_back()
    if plan.action == "wait":
        return adb.wait_seconds(plan.wait_s)
    return "no-op"
