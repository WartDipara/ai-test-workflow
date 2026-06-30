"""通用行为链：由 LLM 规划多步动作，代码只负责安全执行与失败回溯。"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)

BehaviorActionType = Literal["tap_xy", "tap_text", "swipe", "wait", "press_back", "none"]
CoordSourceType = Literal["ocr", "pulse", "vlm_xy", "dialogue_blank", ""]

_TECHNIQUE_SELECTION_RE = re.compile(
    r"technique|selection|技牌|技能选择|三选一",
    re.IGNORECASE,
)
_OCR_MATCH_RADIUS_PX = 100

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
    coord_source: CoordSourceType = ""
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


def _normalize_ocr_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def bbox_for_text_strict(
    bboxes: list[OcrBbox],
    target: str,
    *,
    prefer_xy: tuple[int, int] | None = None,
) -> OcrBbox | None:
    """OCR 文本绑定：精确匹配优先，子串取最长命中；单字歧义用 prefer_xy 消歧。"""
    needle = (target or "").strip()
    if not needle:
        return None
    needle_norm = _normalize_ocr_text(needle)

    exact: list[OcrBbox] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text:
            continue
        text_norm = _normalize_ocr_text(text)
        if text_norm.lower() == needle_norm.lower():
            exact.append(bbox)
    if exact:
        if prefer_xy is not None and len(exact) > 1:
            return min(exact, key=lambda b: math.hypot(b.cx - prefer_xy[0], b.cy - prefer_xy[1]))
        return exact[0]

    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text:
            continue
        text_norm = _normalize_ocr_text(text)
        if needle_norm not in text_norm and text_norm not in needle_norm:
            continue
        if len(needle_norm) <= 1:
            if text_norm == needle_norm:
                candidates.append((len(text_norm), bbox))
            elif prefer_xy is not None and math.hypot(
                bbox.cx - prefer_xy[0], bbox.cy - prefer_xy[1]
            ) <= 180:
                candidates.append((len(text_norm), bbox))
            continue
        candidates.append((len(text_norm), bbox))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    max_len = candidates[0][0]
    group = [bbox for length, bbox in candidates if length == max_len]
    if prefer_xy is not None and len(group) > 1:
        return min(group, key=lambda b: math.hypot(b.cx - prefer_xy[0], b.cy - prefer_xy[1]))
    return group[0]


def bbox_for_text(
    bboxes: list[OcrBbox],
    target: str,
    *,
    prefer_xy: tuple[int, int] | None = None,
) -> OcrBbox | None:
    return bbox_for_text_strict(bboxes, target, prefer_xy=prefer_xy)


def nearest_bbox_distance(x: int, y: int, bboxes: list[OcrBbox]) -> float:
    if x <= 0 or y <= 0 or not bboxes:
        return float("inf")
    return min(math.hypot(x - b.cx, y - b.cy) for b in bboxes)


def tap_xy_matches_ocr(x: int, y: int, bboxes: list[OcrBbox], *, radius_px: int = _OCR_MATCH_RADIUS_PX) -> bool:
    return nearest_bbox_distance(x, y, bboxes) <= radius_px


def _is_technique_selection_text(ocr_summary: str) -> bool:
    return bool(_TECHNIQUE_SELECTION_RE.search(ocr_summary or ""))


def _technique_modal_fingerprint(bboxes: list[OcrBbox], screen_h: int) -> str:
    cutoff = int(screen_h * 0.45) if screen_h > 0 else 0
    texts = sorted(
        (b.text or "").strip()
        for b in bboxes
        if (b.text or "").strip() and b.cy >= cutoff
    )
    return f"technique|{'|'.join(texts[:15])}"[:320]


def behavior_progress_fingerprint(
    *,
    ocr_summary: str,
    stage: str = "",
    bboxes: list[OcrBbox] | None = None,
    screen_h: int = 0,
    scene_hint: str = "",
) -> str:
    hint = f"{scene_hint}|{stage}".lower()
    if bboxes and screen_h > 0:
        if "dialog" in hint:
            from game_agent.services.scene_classifier import compute_scene_fingerprint

            return compute_scene_fingerprint(
                "dialogue",
                ocr_summary=ocr_summary,
                bboxes=bboxes,
                screen_h=screen_h,
            )
        if "tutorial" in hint or _is_technique_selection_text(ocr_summary):
            if _is_technique_selection_text(ocr_summary):
                return _technique_modal_fingerprint(bboxes, screen_h)
            from game_agent.services.scene_classifier import compute_scene_fingerprint

            return compute_scene_fingerprint(
                "tutorial",
                ocr_summary=ocr_summary,
                bboxes=bboxes,
                screen_h=screen_h,
            )
    return f"{stage}|{(ocr_summary or '')[:300]}"


def ocr_progressed(before_ocr: str, after_ocr: str) -> bool:
    from game_agent.services.node_verifier import ocr_text_delta_summary

    delta = ocr_text_delta_summary(before_ocr, after_ocr)
    return bool(delta and delta != "no_text_delta")


def sanitize_dialogue_blank_tap(
    step: BehaviorStep,
    *,
    ocr_summary: str,
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
) -> BehaviorStep:
    from game_agent.services.dialogue_heuristics import (
        dialogue_box_fallback_xy,
        is_blank_continue_cta,
        ocr_has_blank_continue_cta,
    )

    if step.action not in ("tap_xy", "tap_text"):
        return step
    merged = ocr_summary or ""
    target = step.target_text or step.intent or ""
    should_blank = ocr_has_blank_continue_cta(bboxes) or is_blank_continue_cta(merged)
    if not should_blank:
        return step
    if step.action == "tap_text" and is_blank_continue_cta(step.target_text):
        should_blank = True
    elif step.action == "tap_xy":
        for bbox in bboxes:
            if is_blank_continue_cta(bbox.text or ""):
                if abs(step.x - bbox.cx) <= 120 and abs(step.y - bbox.cy) <= 80:
                    should_blank = True
                    break
    if not should_blank:
        return step
    fx, fy = dialogue_box_fallback_xy(screen_w, screen_h)
    return step.model_copy(
        update={
            "action": "tap_xy",
            "x": fx,
            "y": fy,
            "target_text": "",
            "reason": (step.reason or "")[:180] + " [dialogue blank fallback]",
        },
        deep=True,
    )


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
                coord_source=str(item.get("coord_source", "") or "")[:20],  # type: ignore[arg-type]
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


def _is_motion_pulse_step(step: BehaviorStep) -> bool:
    blob = f"{step.reason} {step.intent}".lower()
    return any(
        token in blob
        for token in ("motion_pulse", "pulse_rank", "static_glow", "vlm_pick_rank")
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
        prefer = (cur.x, cur.y) if cur.x > 0 and cur.y > 0 else None
        if cur.action == "tap_text":
            bbox = bbox_for_text(bboxes, cur.target_text, prefer_xy=prefer)
            if bbox is not None:
                cur.action = "tap_xy"
                cur.x = bbox.cx
                cur.y = bbox.cy
        if cur.action == "tap_xy" and (cur.x <= 0 or cur.y <= 0):
            continue
        if cur.action == "tap_xy" and bboxes and not tap_xy_matches_ocr(cur.x, cur.y, bboxes):
            if _is_motion_pulse_step(cur):
                pass
            elif cur.coord_source in ("vlm_xy", "pulse"):
                pass
            elif cur.target_text:
                bbox = bbox_for_text(bboxes, cur.target_text, prefer_xy=(cur.x, cur.y))
                if bbox is not None:
                    cur.x = bbox.cx
                    cur.y = bbox.cy
                else:
                    continue
            else:
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


def behavior_step_from_vlm_analysis(
    analysis: Any,
    *,
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
) -> BehaviorStep | None:
    """从 VLM 融合分析构建单步点击/等待动作。"""
    from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis

    if not isinstance(analysis, InGameScreenAnalysis):
        return None
    action = analysis.recommended_action
    if action in ("none", "wait"):
        return None
    if analysis.tap_confidence < 0.35 and analysis.confidence < 0.4:
        return None

    prefer_xy = (
        (analysis.tap_x, analysis.tap_y)
        if analysis.tap_x > 0 and analysis.tap_y > 0
        else None
    )
    ocr_target = (analysis.semantic_target_text or analysis.tap_target_text or "").strip()
    fused_sources = ("motion_pulse", "motion_ocr_fused")

    if action == "tap_text" and ocr_target:
        bbox = bbox_for_text(bboxes, ocr_target, prefer_xy=prefer_xy)
        if bbox is None and prefer_xy is not None:
            return BehaviorStep(
                id="vlm_fusion_tap",
                action="tap_xy",
                x=clamp_coord(prefer_xy[0], screen_w),
                y=clamp_coord(prefer_xy[1], screen_h),
                target_text=ocr_target[:80],
                coord_source="vlm_xy",
                intent=analysis.fusion_reason[:160] or "vlm fusion tap",
                reason=analysis.fusion_reason[:300],
                success_criteria=[],
            )
        if bbox is None:
            return None
        return BehaviorStep(
            id="vlm_fusion_tap",
            action="tap_xy",
            x=clamp_coord(bbox.cx, screen_w),
            y=clamp_coord(bbox.cy, screen_h),
            target_text=ocr_target[:80],
            coord_source="ocr",
            intent=analysis.fusion_reason[:160] or "vlm fusion tap",
            reason=analysis.fusion_reason[:300],
            success_criteria=[],
        )

    if action == "tap_xy" and analysis.tap_x > 0 and analysis.tap_y > 0:
        x = clamp_coord(analysis.tap_x, screen_w)
        y = clamp_coord(analysis.tap_y, screen_h)
        coord_source: CoordSourceType = "vlm_xy"
        if analysis.tap_source == "dialogue_blank":
            from game_agent.services.dialogue_heuristics import dialogue_box_fallback_xy

            x, y = dialogue_box_fallback_xy(screen_w, screen_h)
            coord_source = "dialogue_blank"
        elif analysis.tap_source in fused_sources:
            coord_source = "pulse" if analysis.tap_source == "motion_pulse" else "vlm_xy"
        elif ocr_target and analysis.tap_source not in fused_sources:
            bbox = bbox_for_text(bboxes, ocr_target, prefer_xy=prefer_xy)
            if bbox is not None:
                x, y = bbox.cx, bbox.cy
                coord_source = "ocr"
        elif bboxes and not tap_xy_matches_ocr(x, y, bboxes):
            if analysis.tap_source not in fused_sources:
                return None
        return BehaviorStep(
            id="vlm_fusion_tap",
            action="tap_xy",
            x=x,
            y=y,
            target_text=ocr_target[:80],
            coord_source=coord_source,
            intent=analysis.fusion_reason[:160] or "vlm fusion tap",
            reason=analysis.fusion_reason[:300],
            success_criteria=[],
        )

    if action == "swipe" and analysis.tap_x > 0 and analysis.tap_y > 0:
        return BehaviorStep(
            id="vlm_fusion_swipe",
            action="swipe",
            x=clamp_coord(analysis.tap_x, screen_w),
            y=clamp_coord(analysis.tap_y, screen_h),
            x2=clamp_coord(analysis.tap_x2, screen_w),
            y2=clamp_coord(analysis.tap_y2, screen_h),
            intent=analysis.fusion_reason[:160] or "vlm fusion swipe",
            reason=analysis.fusion_reason[:300],
            success_criteria=[],
        )
    return None


_DIM_PROTECTED_TAP_STEP_IDS = frozenset({"vlm_fusion_tap"})


def apply_dim_region_to_chain(
    chain: BehaviorChain | None,
    tap_xy: tuple[int, int],
    *,
    reason: str = "dim_region tap",
    protect_confident_taps: bool = True,
) -> BehaviorChain | None:
    if chain is None:
        x, y = tap_xy
        return BehaviorChain(
            steps=[
                BehaviorStep(
                    id="dim_region_tap",
                    action="tap_xy",
                    x=x,
                    y=y,
                    intent=reason[:160],
                    reason=reason[:300],
                ),
                BehaviorStep(id="observe", action="wait", wait_s=1.5, intent="wait after dim tap"),
            ],
            source="dim_region",
            stage="in_game",
            goal="advance dialogue via dim region",
        )
    steps = [s.model_copy(deep=True) for s in chain.steps]
    x, y = tap_xy
    dim_step = BehaviorStep(
        id="dim_region_tap",
        action="tap_xy",
        x=x,
        y=y,
        intent=reason[:160],
        reason=reason[:300],
    )
    replaced = False
    for i, step in enumerate(steps):
        if step.action not in ("tap_xy", "tap_text"):
            continue
        if protect_confident_taps and step.id in _DIM_PROTECTED_TAP_STEP_IDS:
            continue
        steps[i] = dim_step
        replaced = True
        break
    if not replaced:
        if protect_confident_taps and any(
            s.action in ("tap_xy", "tap_text") and s.id in _DIM_PROTECTED_TAP_STEP_IDS for s in steps
        ):
            return chain
        steps.insert(0, dim_step)
    return chain.model_copy(update={"steps": steps, "source": "dim_region"}, deep=True)


def _brain_step_has_resolved_coords(step: BehaviorStep, bboxes: list[OcrBbox]) -> bool:
    if step.action not in ("tap_xy", "tap_text"):
        return False
    if step.x <= 0 or step.y <= 0:
        return False
    if step.coord_source in ("ocr", "vlm_xy", "pulse", "dialogue_blank"):
        return True
    if tap_xy_matches_ocr(step.x, step.y, bboxes):
        return True
    return _is_motion_pulse_step(step)


def merge_vlm_tap_into_chain(
    chain: BehaviorChain | None,
    analysis: Any,
    *,
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
) -> BehaviorChain | None:
    """将 VLM 融合点击建议合并到行为链首步。"""
    vlm_step = behavior_step_from_vlm_analysis(
        analysis,
        bboxes=bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
    )
    if vlm_step is None:
        return chain
    vlm_step = sanitize_dialogue_blank_tap(
        vlm_step,
        ocr_summary="",
        bboxes=bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
    )
    if chain is None or not chain.steps:
        return BehaviorChain(
            steps=[vlm_step, BehaviorStep(id="observe", action="wait", wait_s=1.5, intent="wait after tap")],
            source="vlm_fusion",
            stage="in_game",
            goal="execute VLM fused tap",
        )
    steps = [s.model_copy(deep=True) for s in chain.steps]
    replaced = False
    for i, step in enumerate(steps):
        if step.action in ("tap_xy", "tap_text"):
            if _brain_step_has_resolved_coords(step, bboxes):
                continue
            steps[i] = vlm_step.model_copy(
                update={"id": step.id or vlm_step.id, "success_criteria": list(step.success_criteria)},
                deep=True,
            )
            replaced = True
            break
    if not replaced:
        steps.insert(0, vlm_step)
    return chain.model_copy(update={"steps": steps}, deep=True)

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
