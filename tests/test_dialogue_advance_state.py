"""对话推进状态机单测。"""

from __future__ import annotations

from game_agent.models.dialogue_dim_tap import DialogueDimTapSection
from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.launch_graph_state import empty_launch_graph_state
from game_agent.services.dialogue_advance_state import (
    DIALOGUE_ADVANCE_MODE_DIM,
    get_dialogue_stall_streak,
    record_dialogue_advance_progress,
    should_use_dim_region_tap,
)


def test_should_use_dim_when_mode_latched() -> None:
    state = empty_launch_graph_state()
    state["dialogue_advance_mode"] = DIALOGUE_ADVANCE_MODE_DIM
    cfg = DialogueDimTapSection(stall_threshold=2)
    assert should_use_dim_region_tap(state, cfg=cfg)


def test_should_use_dim_after_stall_and_vlm_hint() -> None:
    state = empty_launch_graph_state()
    state["dialogue_advance_stall_streak"] = 2
    state["scene_gate_use_dim_region_tap"] = True
    cfg = DialogueDimTapSection(stall_threshold=2)
    assert should_use_dim_region_tap(state, cfg=cfg)


def test_record_progress_resets_streak_and_latches_dim() -> None:
    state = empty_launch_graph_state()
    state["dialogue_advance_stall_streak"] = 3
    record_dialogue_advance_progress(state, progressed=True, used_dim_tap=True)
    assert get_dialogue_stall_streak(state) == 0
    assert state["dialogue_advance_mode"] == DIALOGUE_ADVANCE_MODE_DIM


def test_in_game_analysis_dim_hint() -> None:
    state = empty_launch_graph_state()
    state["dialogue_advance_stall_streak"] = 2
    analysis = InGameScreenAnalysis(use_dim_region_tap=True, dim_region_hint="blank continue")
    cfg = DialogueDimTapSection(stall_threshold=2)
    assert should_use_dim_region_tap(state, cfg=cfg, screen_analysis=analysis)
