"""通用行为链：由 LLM 规划多步动作，代码只负责安全执行与失败回溯。"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)

BehaviorActionType = Literal["tap_xy", "tap_text", "swipe", "wait", "press_back", "none"]

_EXIT_CONFIRM_KEYWORDS = (
    "friendly reminder",
    "exit",
    "退出",
    "离开游戏",
    "离开",
    "确认退出",
)


class BehaviorStep(BaseModel):
    """单个通用动作步骤，不绑定选角/战斗/教程等具体业务。"""

    id: str
    action: BehaviorActionType = "none"
    x: int = 0
    y: int = 0
    x2: int = 0
    y2: int = 0
    target_text: str = ""
    wait_s: float = 1.5
    intent: str = ""
    reason: str = ""
    label: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    max_attempts: int = 2
    attempts: int = 0
    done: bool = False

    def signature(self) -> str:
        return (
            f"{self.action}:{self.x}:{self.y}:{self.x2}:{self.y2}:"
            f"{self.target_text}:{self.wait_s:.1f}:{self.intent}"
        )


class BehaviorChain(BaseModel):
    """可执行的短行为链，适用于启动后期和游戏内阶段。"""

    steps: list[BehaviorStep] = Field(default_factory=list)
    source: str = ""
    stage: str = ""
    goal: str = ""
    replan_from_step_id: str = ""
    failure_context: list[dict[str, Any]] = Field(default_factory=list)


class BehaviorFailureTrace(BaseModel):
    step_id: str = ""
    label: str = ""
    intent: str = ""
    action: str = ""
    target_text: str = ""
    attempts: int = 0
    reason: str = ""
    ocr_excerpt: str = ""
    artifact: str = ""


def strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```json"):
        s = s[7:]
    if s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def clamp_coord(value: int, limit: int) -> int:
    if limit <= 0:
        return max(0, value)
    return max(0, min(value, limit))


def bbox_for_text(bboxes: list[OcrBbox], target: str) -> OcrBbox | None:
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


def parse_behavior_chain_json(
    raw: str,
    *,
    screen_w: int = 0,
    screen_h: int = 0,
    max_wait_s: float = 5.0,
    max_steps: int = 7,
) -> BehaviorChain | None:
    try:
        data = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw_steps = data.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        return None

    steps: list[BehaviorStep] = []
    for i, item in enumerate(raw_steps[:max_steps]):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "none") or "none").strip().lower()
        if action not in ("tap_xy", "tap_text", "swipe", "wait", "press_back", "none"):
            continue
        try:
            x = clamp_coord(int(item.get("x", 0) or 0), screen_w)
            y = clamp_coord(int(item.get("y", 0) or 0), screen_h)
            x2 = clamp_coord(int(item.get("x2", 0) or 0), screen_w)
            y2 = clamp_coord(int(item.get("y2", 0) or 0), screen_h)
            wait_s = float(item.get("wait_s", 1.5) or 1.5)
        except (TypeError, ValueError):
            x, y, x2, y2, wait_s = 0, 0, 0, 0, 1.5
        wait_s = max(0.5, min(wait_s, max_wait_s))
        criteria = item.get("success_criteria") or []
        steps.append(
            BehaviorStep(
                id=str(item.get("id", f"step_{i}") or f"step_{i}")[:80],
                action=action,  # type: ignore[arg-type]
                x=x,
                y=y,
                x2=x2,
                y2=y2,
                target_text=str(item.get("target_text", "") or "")[:120],
                wait_s=wait_s,
                intent=str(item.get("intent", "") or "")[:160],
                reason=str(item.get("reason", "") or "")[:300],
                label=str(item.get("label", "") or "")[:80],
                success_criteria=[str(v)[:160] for v in criteria if isinstance(v, str)],
                max_attempts=max(1, min(int(item.get("max_attempts", 2) or 2), 5)),
            ),
        )

    if not steps:
        return None
    return BehaviorChain(
        steps=steps,
        source=str(data.get("source", "vision") or "vision")[:80],
        stage=str(data.get("stage", "") or "")[:80],
        goal=str(data.get("goal", "") or "")[:200],
        replan_from_step_id=str(data.get("replan_from_step_id", "") or "")[:80],
        failure_context=[
            item for item in (data.get("failure_context") or []) if isinstance(item, dict)
        ][:5],
    )


def validate_behavior_chain(
    chain: BehaviorChain,
    *,
    bboxes: list[OcrBbox] | None = None,
    screen_w: int = 0,
    screen_h: int = 0,
) -> BehaviorChain | None:
    if not chain.steps:
        return None
    bboxes = bboxes or []
    sanitized: list[BehaviorStep] = []
    for step in chain.steps:
        cur = step.model_copy(deep=True)
        if cur.action == "tap_text":
            bbox = bbox_for_text(bboxes, cur.target_text)
            if bbox is not None:
                cur.action = "tap_xy"
                cur.x = bbox.cx
                cur.y = bbox.cy
        if cur.action == "tap_xy" and (cur.x <= 0 or cur.y <= 0):
            continue
        if cur.action == "swipe" and (
            cur.x <= 0 or cur.y <= 0 or cur.x2 <= 0 or cur.y2 <= 0
        ):
            continue
        if screen_w > 0:
            cur.x = clamp_coord(cur.x, screen_w)
            cur.x2 = clamp_coord(cur.x2, screen_w)
        if screen_h > 0:
            cur.y = clamp_coord(cur.y, screen_h)
            cur.y2 = clamp_coord(cur.y2, screen_h)
        sanitized.append(cur)
    if not sanitized:
        return None
    return chain.model_copy(update={"steps": sanitized}, deep=True)


def execute_behavior_step(
    step: BehaviorStep,
    *,
    adb,
    sw: int,
    sh: int,
) -> str:
    if step.action == "tap_xy":
        if step.x <= 0 or step.y <= 0:
            return f"refused tap invalid ({step.x},{step.y})"
        return adb.tap(step.x, step.y, width=sw, height=sh)
    if step.action == "swipe":
        if step.x <= 0 or step.y <= 0 or step.x2 <= 0 or step.y2 <= 0:
            return "refused swipe invalid coords"
        return adb.swipe(step.x, step.y, step.x2, step.y2, width=sw, height=sh)
    if step.action == "press_back":
        return adb.press_back()
    if step.action == "wait":
        return adb.wait_seconds(step.wait_s)
    return "no-op"


def behavior_progress_fingerprint(*, ocr_summary: str, stage: str = "") -> str:
    return f"{stage}|{(ocr_summary or '')[:300]}"


def should_downgrade_press_back(ocr_summary: str) -> bool:
    low = (ocr_summary or "").lower()
    return any(keyword in low for keyword in _EXIT_CONFIRM_KEYWORDS)


def press_back_caused_exit_confirm(*, before_ocr: str, after_ocr: str) -> bool:
    return should_downgrade_press_back(after_ocr) and not should_downgrade_press_back(before_ocr)


def evaluate_step_success(
    step: BehaviorStep,
    *,
    before_ocr: str,
    after_ocr: str,
    before_facts: Any | None = None,
) -> tuple[bool, str]:
    del before_facts
    before = (before_ocr or "").lower()
    after = (after_ocr or "").lower()
    criteria = [c.strip() for c in step.success_criteria if (c or "").strip()]
    if not criteria:
        return True, "no_criteria"

    for criterion in criteria:
        low = criterion.lower()
        if low.startswith("!") or "disappear" in low or "消失" in criterion:
            keyword = low.lstrip("!").replace("disappear", "").replace("消失", "").strip()
            if keyword and keyword in before and keyword not in after:
                return True, f"disappeared:{keyword}"
            continue
        if low in after:
            return True, f"appeared:{low}"
        tokens = [token for token in low.replace(",", " ").split() if len(token) >= 2]
        if tokens and all(token in after for token in tokens):
            return True, f"matched:{low}"
    return False, "criteria_not_met"


def sanitize_press_back_step(
    step: BehaviorStep,
    *,
    ocr_summary: str,
) -> BehaviorStep:
    if step.action != "press_back":
        return step
    if should_downgrade_press_back(ocr_summary):
        return step.model_copy(
            update={
                "action": "wait",
                "wait_s": max(step.wait_s, 1.0),
                "reason": (step.reason or "")[:200] + " [press_back downgraded: exit dialog]",
            },
            deep=True,
        )
    return step


def behavior_failure_trace(state: dict[str, Any], *, prefix: str) -> list[dict[str, Any]]:
    raw = state.get(f"{prefix}_failure_trace") or []
    return raw if isinstance(raw, list) else []


def can_replan_behavior_chain(
    state: dict[str, Any],
    *,
    prefix: str,
    max_replans: int,
) -> bool:
    if max_replans <= 0:
        return False
    return int(state.get(f"{prefix}_replan_count") or 0) < max_replans


def record_behavior_chain_failure(
    state: dict[str, Any],
    step: BehaviorStep,
    *,
    prefix: str,
    reason: str,
    ocr_summary: str = "",
    artifact: str = "",
) -> BehaviorFailureTrace:
    trace = BehaviorFailureTrace(
        step_id=step.id,
        label=step.label,
        intent=step.intent,
        action=step.action,
        target_text=step.target_text,
        attempts=step.attempts,
        reason=reason[:300],
        ocr_excerpt=(ocr_summary or "")[:500],
        artifact=artifact,
    )
    traces = behavior_failure_trace(state, prefix=prefix)
    traces.append(trace.model_dump())
    state[f"{prefix}_failure_trace"] = traces[-5:]
    state[f"{prefix}_replan_count"] = int(state.get(f"{prefix}_replan_count") or 0) + 1
    state[f"{prefix}_last_failed_step_id"] = step.id
    logger.warning(
        "[BehaviorChain:%s] failure step=%s intent=%s attempts=%d replan_count=%d reason=%s",
        prefix,
        step.id,
        step.intent[:80],
        step.attempts,
        int(state.get(f"{prefix}_replan_count") or 0),
        reason[:160],
    )
    return trace
