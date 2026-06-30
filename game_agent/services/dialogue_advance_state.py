"""对话暗色区域点击 — 会话状态与门控。"""

from __future__ import annotations

from game_agent.models.dialogue_dim_tap import DialogueDimTapSection
from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.launch_graph_state import LaunchGraphState

DIALOGUE_ADVANCE_MODE_OCR = "ocr"
DIALOGUE_ADVANCE_MODE_DIM = "dim_region"


def dialogue_dim_cfg_from_game(game) -> DialogueDimTapSection:
    if game is None:
        return DialogueDimTapSection()
    return getattr(game, "dialogue_dim_tap", None) or DialogueDimTapSection()


def get_dialogue_advance_mode(state: LaunchGraphState) -> str:
    mode = str(state.get("dialogue_advance_mode") or DIALOGUE_ADVANCE_MODE_OCR).strip()
    return mode if mode in (DIALOGUE_ADVANCE_MODE_OCR, DIALOGUE_ADVANCE_MODE_DIM) else DIALOGUE_ADVANCE_MODE_OCR


def get_dialogue_stall_streak(state: LaunchGraphState) -> int:
    return int(state.get("dialogue_advance_stall_streak") or 0)


def vlm_dim_hint_from_state(state: LaunchGraphState) -> bool:
    if bool(state.get("scene_gate_use_dim_region_tap")):
        return True
    cached = state.get("last_in_game_screen_analysis")
    if isinstance(cached, dict) and cached.get("use_dim_region_tap"):
        return True
    return False


def should_use_dim_region_tap(
    state: LaunchGraphState,
    *,
    cfg: DialogueDimTapSection,
    screen_analysis: InGameScreenAnalysis | None = None,
    force_hint: bool = False,
) -> bool:
    if get_dialogue_advance_mode(state) == DIALOGUE_ADVANCE_MODE_DIM:
        return True
    if get_dialogue_stall_streak(state) < cfg.stall_threshold:
        return False
    if force_hint or vlm_dim_hint_from_state(state):
        return True
    if screen_analysis is not None and screen_analysis.use_dim_region_tap:
        return True
    return False


def record_dialogue_advance_progress(
    state: LaunchGraphState,
    *,
    progressed: bool,
    used_dim_tap: bool,
) -> None:
    if progressed:
        state["dialogue_advance_stall_streak"] = 0
        if used_dim_tap:
            state["dialogue_advance_mode"] = DIALOGUE_ADVANCE_MODE_DIM
        return
    streak = get_dialogue_stall_streak(state) + 1
    state["dialogue_advance_stall_streak"] = streak


def set_dialogue_dim_last_tap(state: LaunchGraphState, x: int, y: int) -> None:
    state["dialogue_dim_last_tap"] = [int(x), int(y)]
