"""进游戏门：OCR 候选列表交给主脑（纯文本 llm）选点，VLM 仅验收。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from game_agent.models.settings import DeepSeekSection, LLMSection
from game_agent.i18n import Concept, compile_lexicon_pattern
from game_agent.services.behavior_chain import strip_json_fence
from game_agent.services.llm_service import build_llm_model
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)

_PRIMARY_ENTER_RE = compile_lexicon_pattern(
    Concept.ENTER_GAME,
    Concept.START_GAME,
)
_BARE_ENTER_RE = compile_lexicon_pattern(Concept.BARE_ENTER)
_HEALTH_ADVISORY_RE = compile_lexicon_pattern(Concept.HEALTH_ADVISORY)
_EXCLUDE_RE = compile_lexicon_pattern(Concept.EXCLUDE_AUTH_CONTEXT)


class EnterGateTapPlan(BaseModel):
    action: Literal["tap_xy", "none"] = "none"
    x: int = 0
    y: int = 0
    target_text: str = ""
    reason: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class EnterGateTapDecision:
    x: int
    y: int
    target_text: str
    reason: str
    source: str


def format_ocr_candidates(bboxes: list[OcrBbox]) -> str:
    rows: list[dict[str, int | str]] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text:
            continue
        rows.append(
            {
                "text": text[:120],
                "cx": bbox.cx,
                "cy": bbox.cy,
                "x1": bbox.x1,
                "y1": bbox.y1,
                "x2": bbox.x2,
                "y2": bbox.y2,
            },
        )
    return json.dumps(rows, ensure_ascii=False, indent=2)


def enter_gate_likely_visible(bboxes: list[OcrBbox], *, ocr_merged: str = "") -> bool:
    """粗判进游戏门是否可能存在（路由用，不选坐标）。"""
    merged = ocr_merged or " ".join(b.text for b in bboxes)
    if _PRIMARY_ENTER_RE.search(merged):
        return True
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text or _EXCLUDE_RE.search(text):
            continue
        if _PRIMARY_ENTER_RE.search(text):
            return True
        if _BARE_ENTER_RE.search(text) and not _HEALTH_ADVISORY_RE.search(text):
            return True
    return False


def _is_health_footer(bbox: OcrBbox, *, screen_h: int) -> bool:
    text = (bbox.text or "").strip()
    if _HEALTH_ADVISORY_RE.search(text):
        return True
    if screen_h > 0 and bbox.cy > int(screen_h * 0.92):
        if _BARE_ENTER_RE.search(text) and not _PRIMARY_ENTER_RE.search(text):
            return True
    return False


def decide_enter_gate_tap_heuristic(
    bboxes: list[OcrBbox],
    *,
    screen_h: int = 0,
) -> EnterGateTapDecision | None:
    """无 LLM 时的启发式：优先完整 CTA 文案，排除底栏健康提示。"""
    primary: list[OcrBbox] = []
    fallback: list[OcrBbox] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text or _EXCLUDE_RE.search(text):
            continue
        if _is_health_footer(bbox, screen_h=screen_h):
            continue
        if _PRIMARY_ENTER_RE.search(text):
            primary.append(bbox)
        elif _BARE_ENTER_RE.search(text):
            fallback.append(bbox)

    picked = primary[0] if primary else (fallback[0] if fallback else None)
    if picked is None:
        return None
    return EnterGateTapDecision(
        x=picked.cx,
        y=picked.cy,
        target_text=picked.text.strip(),
        reason="heuristic:primary_enter_cta" if picked in primary else "heuristic:bare_enter",
        source="heuristic",
    )


def parse_enter_gate_tap_json(raw: str) -> EnterGateTapPlan | None:
    try:
        data = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action", "none") or "none").strip().lower()
    if action not in ("tap_xy", "none"):
        action = "none"
    try:
        x = int(data.get("x", 0) or 0)
        y = int(data.get("y", 0) or 0)
    except (TypeError, ValueError):
        x, y = 0, 0
    return EnterGateTapPlan(
        action=action,  # type: ignore[arg-type]
        x=x,
        y=y,
        target_text=str(data.get("target_text", "") or "")[:120],
        reason=str(data.get("reason", "") or "")[:300],
        source="brain",
    )


def _snap_to_ocr_bbox(
    plan: EnterGateTapPlan,
    bboxes: list[OcrBbox],
) -> EnterGateTapDecision | None:
    if plan.action != "tap_xy" or plan.x <= 0 or plan.y <= 0:
        return None
    needle = (plan.target_text or "").strip()
    if needle:
        for bbox in bboxes:
            text = (bbox.text or "").strip()
            if needle in text or text in needle:
                return EnterGateTapDecision(
                    x=bbox.cx,
                    y=bbox.cy,
                    target_text=text,
                    reason=plan.reason or "brain:matched_target_text",
                    source="brain",
                )
    best: OcrBbox | None = None
    best_dist = 10**9
    for bbox in bboxes:
        dist = abs(bbox.cx - plan.x) + abs(bbox.cy - plan.y)
        if dist < best_dist:
            best_dist = dist
            best = bbox
    if best is not None and best_dist <= 120:
        return EnterGateTapDecision(
            x=best.cx,
            y=best.cy,
            target_text=best.text.strip(),
            reason=plan.reason or "brain:snap_nearest_ocr",
            source="brain",
        )
    return EnterGateTapDecision(
        x=plan.x,
        y=plan.y,
        target_text=needle,
        reason=plan.reason or "brain:raw_coords",
        source="brain",
    )


async def decide_enter_gate_tap(
    *,
    llm_cfg: LLMSection | None,
    bboxes: list[OcrBbox],
    ocr_summary: str = "",
    stage_hint: str = "",
    screen_w: int = 0,
    screen_h: int = 0,
    deepseek: DeepSeekSection | None = None,
    prior_failure: str = "",
) -> EnterGateTapDecision | None:
    heuristic = decide_enter_gate_tap_heuristic(bboxes, screen_h=screen_h)
    if llm_cfg is None:
        return heuristic

    candidates = format_ocr_candidates(bboxes)
    avoid = ""
    if prior_failure.strip():
        avoid = f"\nPrevious tap did not progress: {prior_failure[:200]}. Pick a different OCR row.\n"

    prompt = f"""
You are the main brain for an Android game launch flow at the enter-game gate.
Pick exactly ONE OCR text row to tap now. Button labels vary by game — do not assume fixed wording.

Stage context: {stage_hint or "server_select / enter game gate"}
Screen size: {screen_w}x{screen_h}

OCR candidates (JSON list of text + coordinates):
{candidates}

OCR summary (x,y text):
{ocr_summary[:2000]}
{avoid}

Rules:
- Prefer the primary CTA to start playing (e.g. 进入游戏, 开始游戏, Enter Game).
- Do NOT tap health advisories, copyright footers, privacy/terms lines, or PK agreement rows.
- Coordinates must come from one of the OCR candidates above.
- If nothing safe to tap, return action=none.

JSON only:
{{
  "action": "tap_xy | none",
  "x": 0,
  "y": 0,
  "target_text": "exact OCR text chosen",
  "reason": "one sentence"
}}
"""
    try:
        model = build_llm_model(llm_cfg, deepseek=deepseek)
        agent = Agent(model, output_type=EnterGateTapPlan)
        result = await agent.run(prompt)
        plan = result.output
        if plan.action == "tap_xy" and plan.x > 0 and plan.y > 0:
            snapped = _snap_to_ocr_bbox(plan, bboxes)
            if snapped is not None:
                logger.info(
                    "[EnterGate] brain_pick label=%r xy=(%d,%d) reason=%s",
                    snapped.target_text[:60],
                    snapped.x,
                    snapped.y,
                    snapped.reason[:120],
                )
                return snapped
    except Exception:
        logger.exception("[EnterGate] main brain tap decision failed")

    if heuristic is not None:
        logger.info(
            "[EnterGate] fallback_heuristic label=%r xy=(%d,%d)",
            heuristic.target_text[:60],
            heuristic.x,
            heuristic.y,
        )
    return heuristic
