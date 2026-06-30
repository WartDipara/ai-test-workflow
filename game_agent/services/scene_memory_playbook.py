"""场景原型识别、指纹与坐标解析。"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.scene_memory import (
    MemoryActionResolver,
    SceneArchetype,
    SceneMemoryAction,
    SceneMemoryEntry,
)
from game_agent.services.behavior_chain import BehaviorChain, BehaviorStep, _technique_modal_fingerprint
from game_agent.services.dialogue_heuristics import (
    ocr_has_blank_continue_cta,
    score_dialogue_from_bboxes,
)
from game_agent.services.technique_selection_heuristics import is_technique_selection_screen
from game_agent.services.tutorial_intent import has_pulse_guidance_phrase
from game_agent.utils.ocr_util import OcrBbox

_NUM_TOKEN_RE = re.compile(r"^[\d.,+%:/\s]+$")
_CARD_BAND_Y_MIN = 0.32
_CARD_BAND_Y_MAX = 0.72


def detect_scene_archetype(
    ocr_summary: str,
    *,
    bboxes: list[OcrBbox] | None = None,
    screen_analysis: InGameScreenAnalysis | None = None,
    screen_h: int = 0,
) -> SceneArchetype:
    if is_technique_selection_screen(ocr_summary, screen_analysis=screen_analysis):
        return "technique_selection"
    if has_pulse_guidance_phrase(ocr_summary):
        return "unknown"
    if bboxes and ocr_has_blank_continue_cta(bboxes):
        return "dialogue_blank_continue"
    if screen_analysis is not None and screen_analysis.use_dim_region_tap:
        return "dialogue_blank_continue"
    if screen_analysis is not None and screen_analysis.ui_stage == "dialog":
        return "dialogue_narrative"
    if bboxes and screen_h > 0:
        score, _ = score_dialogue_from_bboxes(bboxes, screen_h=screen_h)
        if score >= 0.55:
            return "dialogue_narrative"
    return "unknown"


def _normalize_fingerprint_tokens(fp: str) -> set[str]:
    tokens: set[str] = set()
    for raw in (fp or "").lower().split("|"):
        t = raw.strip()
        if not t or _NUM_TOKEN_RE.match(t):
            continue
        t = re.sub(r"\+\d+%?", "", t)
        t = re.sub(r"\d+", "", t).strip()
        if len(t) >= 2:
            tokens.add(t)
    return tokens


def compute_structural_fingerprint(
    archetype: SceneArchetype,
    *,
    ocr_summary: str,
    bboxes: list[OcrBbox],
    screen_h: int,
) -> str:
    if archetype == "technique_selection" and screen_h > 0:
        return _technique_modal_fingerprint(bboxes, screen_h)
    if archetype == "dialogue_blank_continue":
        return "dialogue_blank|dim_overlay"
    if archetype == "dialogue_narrative":
        bottom = sorted(
            (b.text or "").strip()[:40]
            for b in bboxes
            if screen_h <= 0 or b.cy >= int(screen_h * 0.5)
        )
        return f"dialogue_narr|{'|'.join(bottom[:8])}"[:320]
    return f"unknown|{(ocr_summary or '')[:200]}"[:320]


def fingerprint_similarity(left: str, right: str) -> float:
    a = _normalize_fingerprint_tokens(left)
    b = _normalize_fingerprint_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def resolve_center_card_column(
    bboxes: list[OcrBbox],
    *,
    screen_w: int,
    screen_h: int,
) -> tuple[int, int] | None:
    if screen_h <= 0 or screen_w <= 0:
        return None
    y_min = int(screen_h * _CARD_BAND_Y_MIN)
    y_max = int(screen_h * _CARD_BAND_Y_MAX)
    candidates = [
        b for b in bboxes
        if y_min <= b.cy <= y_max and len((b.text or "").strip()) >= 3
    ]
    if not candidates:
        return None
    mid_x = screen_w // 2
    center_band = [b for b in candidates if abs(b.cx - mid_x) <= screen_w * 0.18]
    if not center_band:
        center_band = sorted(candidates, key=lambda b: abs(b.cx - mid_x))[:3]
    anchor = min(center_band, key=lambda b: abs(b.cx - mid_x))
    return anchor.cx, anchor.cy


def resolve_memory_action(
    action: SceneMemoryAction,
    *,
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
    dim_xy: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    if action.resolver == "dim_region" and dim_xy is not None:
        return dim_xy
    if action.resolver == "center_card_column":
        xy = resolve_center_card_column(bboxes, screen_w=screen_w, screen_h=screen_h)
        if xy is not None:
            return xy
    if action.resolver == "screen_ratio" and action.x_ratio > 0 and action.y_ratio > 0:
        return int(action.x_ratio * screen_w), int(action.y_ratio * screen_h)
    if action.x > 0 and action.y > 0:
        return action.x, action.y
    return None


def build_chain_from_memory(
    entry: SceneMemoryEntry,
    *,
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
    dim_xy: tuple[int, int] | None = None,
) -> BehaviorChain | None:
    xy = resolve_memory_action(
        entry.primary_action,
        bboxes=bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
        dim_xy=dim_xy,
    )
    if xy is None:
        return None
    x, y = xy
    steps = [
        BehaviorStep(
            id="scene_memory_tap",
            action="tap_xy",
            x=x,
            y=y,
            intent=entry.primary_action.intent or f"scene_memory:{entry.archetype}",
            reason=f"memory:{entry.memory_id}",
            success_criteria=["screen progresses"],
        ),
        BehaviorStep(
            id="scene_memory_wait",
            action="wait",
            wait_s=max(0.5, entry.primary_action.wait_s),
            intent="wait after scene memory tap",
        ),
    ]
    return BehaviorChain(
        source="scene_memory",
        stage="in_game",
        goal=f"fast_path:{entry.archetype}",
        steps=steps,
    )


def verify_memory_progress(
    archetype: SceneArchetype,
    *,
    before_ocr: str,
    after_ocr: str,
    screen_analysis: InGameScreenAnalysis | None = None,
) -> bool:
    if archetype == "technique_selection":
        return not is_technique_selection_screen(after_ocr, screen_analysis=screen_analysis)
    if archetype == "dialogue_blank_continue":
        from game_agent.services.behavior_chain import ocr_progressed

        return ocr_progressed(before_ocr, after_ocr)
    if archetype == "dialogue_narrative":
        from game_agent.services.behavior_chain import ocr_progressed

        return ocr_progressed(before_ocr, after_ocr)
    return False


def new_memory_id(archetype: str, fingerprint: str) -> str:
    raw = f"{archetype}|{fingerprint}|{datetime.now(tz=UTC).isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def memory_action_from_step(
    step: BehaviorStep,
    *,
    screen_w: int,
    screen_h: int,
    archetype: SceneArchetype,
) -> SceneMemoryAction:
    resolver: MemoryActionResolver = "fixed_xy"
    if archetype == "technique_selection":
        resolver = "center_card_column"
    elif step.id == "dim_region_tap":
        resolver = "dim_region"
    x_ratio = (step.x / screen_w) if screen_w > 0 and step.x > 0 else 0.0
    y_ratio = (step.y / screen_h) if screen_h > 0 and step.y > 0 else 0.0
    if x_ratio > 0 and y_ratio > 0:
        resolver = "screen_ratio"
    return SceneMemoryAction(
        action="tap_xy",
        resolver=resolver,
        x=step.x,
        y=step.y,
        x_ratio=round(x_ratio, 4),
        y_ratio=round(y_ratio, 4),
        intent=step.intent[:160],
    )
