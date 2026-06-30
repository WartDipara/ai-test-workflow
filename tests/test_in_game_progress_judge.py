"""局内 VLM 无进展 streak 与规则判断单测。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.launch_graph_state import empty_launch_graph_state
from game_agent.models.settings import AppConfig, GameSection
from game_agent.services.in_game_progress_judge import (
    apply_vlm_no_progress_streak,
    update_streak_after_action,
    vlm_session_progressed,
)


def _analysis(**kwargs) -> InGameScreenAnalysis:
    return InGameScreenAnalysis(confidence=0.8, **kwargs)


def _cfg(**game_overrides) -> AppConfig:
    game = {"package_name": "com.test.game", **game_overrides}
    return AppConfig.model_validate(
        {
            "llm": {"base_url": "http://x", "api_key": "k", "model_name": "m"},
            "llm_multimodal": {"base_url": "http://x", "api_key": "k", "model_name": "v"},
            "game": game,
        }
    )


def test_vlm_session_progressed_guidance_cleared() -> None:
    before = _analysis(forced_guidance_present=True, ui_stage="tutorial", screen_static=True)
    after = _analysis(forced_guidance_present=False, ui_stage="hud", screen_static=False)
    assert vlm_session_progressed(before, after, min_confidence=0.55) is True


def test_vlm_session_progressed_static_tutorial_unchanged() -> None:
    before = _analysis(
        forced_guidance_present=True,
        ui_stage="tutorial",
        screen_static=True,
    )
    after = _analysis(
        forced_guidance_present=True,
        ui_stage="tutorial",
        screen_static=True,
    )
    assert vlm_session_progressed(before, after, min_confidence=0.55) is False


def test_vlm_session_progressed_loading_uncertain() -> None:
    before = _analysis(forced_guidance_present=True, ui_stage="tutorial")
    after = InGameScreenAnalysis(loading_or_blocking=True, confidence=0.9)
    assert vlm_session_progressed(before, after, min_confidence=0.55) is None


def test_apply_vlm_no_progress_streak_force_fail_at_threshold() -> None:
    state = empty_launch_graph_state()
    threshold = 10
    for i in range(threshold - 1):
        streak, force_fail = apply_vlm_no_progress_streak(
            state, False, fail_threshold=threshold
        )
        assert streak == i + 1
        assert force_fail is False
    streak, force_fail = apply_vlm_no_progress_streak(
        state, False, fail_threshold=threshold
    )
    assert streak == threshold
    assert force_fail is True


def test_apply_vlm_no_progress_streak_resets_on_progress() -> None:
    state = empty_launch_graph_state()
    apply_vlm_no_progress_streak(state, False, fail_threshold=10)
    apply_vlm_no_progress_streak(state, False, fail_threshold=10)
    assert state["in_game_vlm_no_progress_streak"] == 2
    streak, force_fail = apply_vlm_no_progress_streak(state, True, fail_threshold=10)
    assert streak == 0
    assert force_fail is False


def test_update_streak_after_action_skips_wait() -> None:
    state = empty_launch_graph_state()
    cfg = _cfg()

    async def _run():
        return await update_streak_after_action(
            state,
            cfg=cfg,
            before=None,
            after_shot=Path("after.png"),
            before_ocr="",
            after_ocr="",
            action="wait",
        )

    streak, force_fail, reason = asyncio.run(_run())
    assert streak == 0
    assert force_fail is False
    assert reason == "skip_wait"


def test_update_streak_after_action_increments_on_no_progress() -> None:
    state = empty_launch_graph_state()
    cfg = _cfg(in_game_post_action_vlm_analyze=False)
    before = _analysis(forced_guidance_present=True, ui_stage="tutorial", screen_static=True)
    after = _analysis(
        forced_guidance_present=True,
        ui_stage="tutorial",
        screen_static=True,
    )

    async def _run():
        with patch(
            "game_agent.services.in_game_progress_judge.evaluate_in_game_session_progress",
            new=AsyncMock(
                return_value=type(
                    "E",
                    (),
                    {
                        "progressed": False,
                        "reason": "stuck",
                        "source": "rules",
                        "after_analysis": after,
                    },
                )()
            ),
        ):
            return await update_streak_after_action(
                state,
                cfg=cfg,
                before=before,
                after_shot=Path("after.png"),
                before_ocr="a",
                after_ocr="a",
                action="tap_xy",
            )

    streak, force_fail, _ = asyncio.run(_run())
    assert streak == 1
    assert force_fail is False


def test_game_section_vlm_no_progress_defaults() -> None:
    g = GameSection()
    assert g.in_game_vlm_no_progress_fail_rounds == 10
    assert g.in_game_vlm_progress_min_confidence == 0.55
    assert g.in_game_post_action_vlm_analyze is True


def test_empty_state_vlm_streak_default() -> None:
    state = empty_launch_graph_state()
    assert state["in_game_vlm_no_progress_streak"] == 0
    assert state["last_in_game_progress_analysis"] == {}
