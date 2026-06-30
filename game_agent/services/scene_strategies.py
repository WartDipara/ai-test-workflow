"""场景策略：按 scene_id 规划低成本动作。"""

from __future__ import annotations

import re
from pathlib import Path

from game_agent.graphs.launch_state_store import completed_tree_node, is_login_done
from game_agent.graphs.static_priority import has_pending_static_work
from game_agent.graphs.launch_phase import is_pre_login_scene_allowed
from game_agent.models.launch_graph_state import LaunchFacts, LaunchGraphState
from game_agent.models.scene import (
    SCENE_STRATEGY_IDS,
    SceneActionPlan,
    SceneClassification,
    SceneTransition,
)
from game_agent.services.dialogue_heuristics import (
    dialogue_box_fallback_xy,
    is_blank_continue_cta,
    pick_dialogue_advance_bbox,
)
from game_agent.utils.ocr_util import OcrBbox
from game_agent.i18n import Concept, compile_lexicon_pattern
from game_agent.models.motion_probe import MotionProbeResult
from game_agent.services.tutorial_intent import needs_visual_tap_locator
from game_agent.services.tutorial_pulse_locator import resolve_tutorial_visual_tap

_SKIP_RE = compile_lexicon_pattern(Concept.SKIP)
_CONTINUE_RE = compile_lexicon_pattern(Concept.CONTINUE)
_CONFIRM_RE = re.compile(
    rf"^(?:{compile_lexicon_pattern(Concept.CONFIRM, Concept.AGREE).pattern})$",
    re.IGNORECASE,
)

_SCENE_LOW_CONFIDENCE_DEACTIVATE_STREAK = 2


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


def _safe_dialogue_tap(screen_w: int, screen_h: int) -> tuple[int, int]:
    return dialogue_box_fallback_xy(screen_w, screen_h)


def plan_dialogue_action(
    bboxes: list[OcrBbox],
    *,
    ocr_summary: str,
    screen_w: int,
    screen_h: int,
    transition: SceneTransition,
    advance_mode: str = "ocr",
    dim_tap_xy: tuple[int, int] | None = None,
) -> SceneActionPlan:
    if transition.kind == "animation_or_loading":
        return SceneActionPlan(
            action="wait",
            wait_s=2.0,
            reason="dialogue:wait_observe_animation",
            mode="wait_observe",
        )

    if advance_mode == "dim_region" and dim_tap_xy is not None:
        x, y = dim_tap_xy
        return SceneActionPlan(
            action="tap_xy",
            x=x,
            y=y,
            reason="dialogue:dim_region",
            mode="dim_advance",
        )

    skip_bbox = _bbox_for_pattern(bboxes, _SKIP_RE)
    if skip_bbox is not None:
        return SceneActionPlan(
            action="tap_xy",
            x=skip_bbox.cx,
            y=skip_bbox.cy,
            target_text=skip_bbox.text.strip(),
            reason="dialogue:heuristic_skip",
            mode="advance",
        )

    narrative_bbox = pick_dialogue_advance_bbox(bboxes, screen_h=screen_h)
    if narrative_bbox is not None and is_blank_continue_cta(narrative_bbox.text or ""):
        narrative_bbox = None
    if narrative_bbox is not None:
        return SceneActionPlan(
            action="tap_xy",
            x=narrative_bbox.cx,
            y=narrative_bbox.cy,
            target_text=narrative_bbox.text.strip(),
            reason="dialogue:tap_narrative_box",
            mode="advance",
        )

    for pattern, label in (
        (_CONTINUE_RE, "continue"),
        (_CONFIRM_RE, "confirm"),
    ):
        bbox = _bbox_for_pattern(bboxes, pattern)
        if bbox is not None:
            if is_blank_continue_cta(bbox.text or ""):
                continue
            return SceneActionPlan(
                action="tap_xy",
                x=bbox.cx,
                y=bbox.cy,
                target_text=bbox.text.strip(),
                reason=f"dialogue:heuristic_{label}",
                mode="advance",
            )

    x, y = _safe_dialogue_tap(screen_w, screen_h)
    return SceneActionPlan(
        action="tap_xy",
        x=x,
        y=y,
        reason="dialogue:dialogue_box_fallback",
        mode="advance",
    )


def plan_tutorial_action(
    bboxes: list[OcrBbox],
    *,
    ocr_summary: str,
    screen_w: int,
    screen_h: int,
    transition: SceneTransition,
    screenshot_path: Path | str | None = None,
    motion_result: MotionProbeResult | None = None,
) -> SceneActionPlan:
    if transition.kind == "animation_or_loading":
        return SceneActionPlan(
            action="wait",
            wait_s=2.0,
            reason="tutorial:wait_observe",
            mode="wait_observe",
        )

    for pattern, label in (
        (_CONTINUE_RE, "continue"),
        (_SKIP_RE, "skip"),
        (_CONFIRM_RE, "confirm"),
    ):
        bbox = _bbox_for_pattern(bboxes, pattern)
        if bbox is not None:
            return SceneActionPlan(
                action="tap_xy",
                x=bbox.cx,
                y=bbox.cy,
                target_text=bbox.text.strip(),
                reason=f"tutorial:heuristic_{label}",
                mode="advance",
            )

    if needs_visual_tap_locator(ocr_summary, bboxes):
        shot = Path(screenshot_path) if screenshot_path else None
        tap = resolve_tutorial_visual_tap(
            motion=motion_result,
            screenshot_path=shot if shot and shot.is_file() else None,
            screen_w=screen_w,
            screen_h=screen_h,
            ocr_summary=ocr_summary,
            bboxes=bboxes,
            vlm_pick=None,
        )
        if tap is not None:
            return SceneActionPlan(
                action="tap_xy",
                x=tap.x,
                y=tap.y,
                reason=f"tutorial:{tap.reason}",
                mode="advance",
            )
        return SceneActionPlan(
            action="wait",
            wait_s=1.5,
            reason="tutorial:pulse_not_found",
            mode="wait_observe",
        )

    x, y = _safe_dialogue_tap(screen_w, screen_h)
    return SceneActionPlan(
        action="tap_xy",
        x=x,
        y=y,
        reason="tutorial:safe_tap",
        mode="advance",
    )


def plan_loading_action(*, transition: SceneTransition) -> SceneActionPlan:
    _ = transition
    return SceneActionPlan(
        action="wait",
        wait_s=2.5,
        reason="loading:observe",
        mode="wait_observe",
    )


def is_pre_login_passive_wait(
    state: LaunchGraphState,
    facts: LaunchFacts,
    *,
    scene_id: str,
    confidence: float,
) -> bool:
    """登录前冷启动过渡：loading 或隐私弹窗刚关闭后的稀疏 unknown 屏，应 wait 而非 recover。"""
    if is_login_done(state):
        return False
    if state.get("in_game_confirmed") or state.get("in_game_entry_passed"):
        return False
    if facts.login_blocking or facts.sub_account_blocking or facts.initial_privacy_dialog:
        return False
    if has_pending_static_work(state, facts):
        return False
    if scene_id == "loading" and confidence >= 0.55:
        return True
    if (
        completed_tree_node(state, "handle_initial_privacy_dialog")
        and scene_id == "unknown"
        and confidence < 0.55
        and not facts.terms_checkbox_visible
    ):
        return True
    return False


def plan_scene_action(
    scene_id: str,
    bboxes: list[OcrBbox],
    *,
    ocr_summary: str,
    screen_w: int,
    screen_h: int,
    transition: SceneTransition,
    advance_mode: str = "ocr",
    dim_tap_xy: tuple[int, int] | None = None,
    screenshot_path: Path | str | None = None,
    motion_result: MotionProbeResult | None = None,
) -> SceneActionPlan:
    if scene_id == "dialogue":
        return plan_dialogue_action(
            bboxes,
            ocr_summary=ocr_summary,
            screen_w=screen_w,
            screen_h=screen_h,
            transition=transition,
            advance_mode=advance_mode,
            dim_tap_xy=dim_tap_xy,
        )
    if scene_id == "tutorial":
        return plan_tutorial_action(
            bboxes,
            ocr_summary=ocr_summary,
            screen_w=screen_w,
            screen_h=screen_h,
            transition=transition,
            screenshot_path=screenshot_path,
            motion_result=motion_result,
        )
    if scene_id == "loading":
        return plan_loading_action(transition=transition)
    return SceneActionPlan(action="none", reason=f"no_strategy_for:{scene_id}")


def should_activate_scene_strategy(
    state: LaunchGraphState,
    classification: SceneClassification,
    facts: LaunchFacts,
) -> bool:
    if state.get("session_agent_active"):
        return False
    if is_pre_login_passive_wait(
        state,
        facts,
        scene_id=classification.scene_id,
        confidence=classification.confidence,
    ):
        return True
    if is_pre_login_scene_allowed(
        state,
        facts,
        scene_id=classification.scene_id,
        confidence=classification.confidence,
    ):
        return True
    if not state.get("login_done"):
        return False
    if state.get("in_game_confirmed") or state.get("in_game_entry_passed"):
        return False
    if facts.login_blocking or facts.sub_account_blocking or facts.initial_privacy_dialog:
        return False
    if has_pending_static_work(state, facts):
        return False
    if classification.scene_id not in SCENE_STRATEGY_IDS:
        return False
    return classification.confidence >= 0.55


def should_deactivate_scene_strategy(
    state: LaunchGraphState,
    classification: SceneClassification,
    facts: LaunchFacts,
    transition: SceneTransition,
) -> bool:
    if not state.get("scene_strategy_active"):
        return False

    if transition.kind == "exit_to_game":
        return True
    if transition.kind == "blocking_popup":
        return True
    if has_pending_static_work(state, facts):
        clear_scene_strategy(state)
        return True
    if transition.kind == "animation_or_loading":
        return False

    if transition.kind == "scene_changed":
        if classification.scene_id in SCENE_STRATEGY_IDS and classification.confidence >= 0.55:
            return False
        if classification.scene_id in ("character_creation", "character_select", "blocking_popup"):
            return True
        if classification.scene_id == "in_game_hud":
            return True
        if classification.scene_id == "unknown":
            return True
        return classification.scene_id not in SCENE_STRATEGY_IDS

    if transition.kind == "low_confidence":
        streak = int(state.get("scene_low_confidence_streak") or 0) + 1
        state["scene_low_confidence_streak"] = streak
        return streak >= _SCENE_LOW_CONFIDENCE_DEACTIVATE_STREAK

    if classification.scene_id == "unknown" and not state.get("active_scene_strategy"):
        return True

    _ = facts
    return False


def apply_scene_classification(
    state: LaunchGraphState,
    classification: SceneClassification,
    transition: SceneTransition,
    facts: LaunchFacts,
) -> None:
    """写入场景上下文并维护策略激活/降级状态。"""
    prev_id = str(state.get("scene_id") or "unknown")
    state["scene_id"] = classification.scene_id
    state["scene_confidence"] = classification.confidence
    state["scene_evidence"] = classification.evidence[:300]
    state["scene_fingerprint"] = classification.fingerprint
    state["scene_transition"] = transition.kind
    state["scene_transition_reason"] = transition.reason[:200]

    facts_dump = dict(state.get("facts") or {})
    facts_dump["scene_id"] = classification.scene_id
    facts_dump["scene_confidence"] = classification.confidence
    state["facts"] = facts_dump

    if transition.kind != "low_confidence":
        state["scene_low_confidence_streak"] = 0

    if should_deactivate_scene_strategy(state, classification, facts, transition):
        clear_scene_strategy(state)
        return

    if should_activate_scene_strategy(state, classification, facts):
        state["scene_strategy_active"] = True
        label_slug = str(state.get("scene_label_slug") or "").strip()
        state["active_scene_strategy"] = label_slug or classification.scene_id
        return

    if (
        state.get("scene_strategy_active")
        and str(state.get("active_scene_strategy") or "") == "loading"
        and classification.scene_id in ("dialogue", "tutorial")
        and classification.confidence >= 0.45
    ):
        state["active_scene_strategy"] = classification.scene_id
        return

    if (
        state.get("scene_strategy_active")
        and classification.scene_id in SCENE_STRATEGY_IDS
        and classification.confidence >= 0.55
    ):
        state["active_scene_strategy"] = classification.scene_id


def clear_scene_strategy(state: LaunchGraphState) -> None:
    state["scene_strategy_active"] = False
    state["active_scene_strategy"] = ""
    state["scene_low_confidence_streak"] = 0
