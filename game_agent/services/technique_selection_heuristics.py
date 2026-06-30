"""局内技牌/选卡画面启发式 — 与对话暗色点击兜底区分。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
    from game_agent.models.launch_graph_state import LaunchGraphState

_TECHNIQUE_RE = re.compile(r"technique", re.IGNORECASE)
_SELECTION_RE = re.compile(r"selection", re.IGNORECASE)
_TECHNIQUE_SIGNAL_RE = re.compile(r"technique", re.IGNORECASE)

_VLM_TAP_CONFIDENCE_FLOOR = 0.35


def is_technique_selection_screen(
    ocr_summary: str = "",
    *,
    screen_analysis: InGameScreenAnalysis | None = None,
) -> bool:
    """OCR 或 VLM 信号表明当前为 Technique Selection 选卡弹窗。"""
    merged = (ocr_summary or "").strip()
    if _TECHNIQUE_RE.search(merged) and _SELECTION_RE.search(merged):
        return True
    if screen_analysis is not None:
        for sig in screen_analysis.guidance_signals:
            if _TECHNIQUE_SIGNAL_RE.search(sig or ""):
                return True
        obs = f"{screen_analysis.observations} {screen_analysis.progress_observation}"
        if _TECHNIQUE_RE.search(obs) and _SELECTION_RE.search(obs):
            return True
    return False


def has_confident_vlm_tap(screen_analysis: InGameScreenAnalysis | None) -> bool:
    if screen_analysis is None:
        return False
    if screen_analysis.use_dim_region_tap:
        return False
    if screen_analysis.tap_confidence < _VLM_TAP_CONFIDENCE_FLOOR:
        return False
    if screen_analysis.recommended_action not in ("tap_xy", "tap_text"):
        return False
    return screen_analysis.tap_x > 0 and screen_analysis.tap_y > 0


def should_skip_dim_region_tap(
    *,
    ocr_summary: str = "",
    screen_analysis: InGameScreenAnalysis | None = None,
) -> bool:
    """暗色区域兜底不得覆盖技牌选卡或高置信 VLM 融合点击。"""
    if is_technique_selection_screen(ocr_summary, screen_analysis=screen_analysis):
        return True
    if screen_analysis is None:
        return False
    ui_stage = screen_analysis.ui_stage
    if ui_stage in ("tutorial", "combat", "hud") and has_confident_vlm_tap(screen_analysis):
        return True
    if has_confident_vlm_tap(screen_analysis):
        guidance = {s.lower() for s in screen_analysis.guidance_signals}
        if screen_analysis.forced_guidance_present and (
            guidance & {"technique_selection_modal", "technique_selection_panel", "three_technique_cards"}
        ):
            return True
    return False


def should_allow_dim_region_in_game(
    *,
    ui_stage: str,
    ocr_summary: str = "",
    screen_analysis: InGameScreenAnalysis | None,
    state: LaunchGraphState | dict,
) -> bool:
    """
    局内暗色点击门控：仅 dialog 阶段或显式 VLM/SceneGate 提示；
    不因 scene_id==dialogue 单独触发。
    """
    from game_agent.services.dialogue_advance_state import vlm_dim_hint_from_state

    if should_skip_dim_region_tap(
        ocr_summary=ocr_summary,
        screen_analysis=screen_analysis,
    ):
        return False
    if ui_stage == "dialog":
        return True
    if screen_analysis is not None and screen_analysis.use_dim_region_tap:
        return True
    if vlm_dim_hint_from_state(state):
        return True
    return False
