"""主脑局内决策与 streak 确认。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.in_game_session_decision import InGameSessionPlan
from game_agent.models.launch_graph_state import empty_launch_graph_state
from game_agent.models.settings import AppConfig, GameSection
from game_agent.services.in_game_brain_decision import apply_brain_decision_to_state
from game_agent.services.in_game_session_planner import decide_in_game_session_round


def _cfg() -> AppConfig:
    return AppConfig.model_validate(
        {
            "llm": {"base_url": "http://x", "api_key": "k", "model_name": "m"},
            "llm_multimodal": {"base_url": "http://x", "api_key": "k", "model_name": "v"},
            "game": {"package_name": "com.test.game"},
        }
    )


def test_brain_success_streak_confirms() -> None:
    state = empty_launch_graph_state()
    cfg = _cfg()
    decision = MagicMock()
    decision.verdict = "success"
    decision.confidence = 0.9
    decision.reason = "HUD free"
    decision.analysis = "ok"
    decision.model_dump.return_value = {
        "verdict": "success",
        "confidence": 0.9,
        "reason": "HUD free",
    }

    r1 = apply_brain_decision_to_state(state, decision, cfg=cfg, round_id=1)
    r2 = apply_brain_decision_to_state(state, decision, cfg=cfg, round_id=2)
    assert r1.success_confirmed is False
    assert r1.success_streak == 1
    assert r2.success_confirmed is True
    assert r2.success_streak == 2


def test_brain_fail_streak_resets_on_continue() -> None:
    state = empty_launch_graph_state()
    cfg = _cfg()
    fail = MagicMock()
    fail.verdict = "fail"
    fail.confidence = 0.85
    fail.reason = "stuck"
    fail.analysis = ""
    fail.model_dump.return_value = {"verdict": "fail", "confidence": 0.85}

    apply_brain_decision_to_state(state, fail, cfg=cfg, round_id=1)
    assert state["in_game_brain_fail_streak"] == 1

    cont = MagicMock()
    cont.verdict = "continue"
    cont.confidence = 0.8
    cont.reason = "retry"
    cont.analysis = ""
    cont.model_dump.return_value = {"verdict": "continue"}

    apply_brain_decision_to_state(state, cont, cfg=cfg, round_id=2)
    assert state["in_game_brain_fail_streak"] == 0


def test_heuristic_success_without_llm() -> None:
    analysis = InGameScreenAnalysis(
        forced_guidance_present=False,
        ui_stage="hud",
        loading_or_blocking=False,
        confidence=0.8,
    )

    async def _run():
        return await decide_in_game_session_round(
            llm_cfg=None,
            deepseek=None,
            bboxes=[],
            ocr_summary="HP bar",
            screen_analysis=analysis,
            round_id=1,
            elapsed_s=30.0,
            screen_w=1080,
            screen_h=1920,
        )

    decision = asyncio.run(_run())
    assert decision.verdict == "success"
    assert decision.source == "heuristic"


def test_game_section_brain_defaults() -> None:
    g = GameSection()
    assert g.in_game_success_confirm_rounds == 2
    assert g.in_game_fail_confirm_rounds == 2
    assert g.in_game_vlm_no_progress_fail_rounds == 10


def test_brain_planner_continue_with_steps() -> None:
    plan = InGameSessionPlan(
        verdict="continue",
        confidence=0.9,
        reason="tap guide",
        goal="follow tutorial",
        steps=[
            {
                "id": "s1",
                "action": "tap_xy",
                "x": 100,
                "y": 200,
                "intent": "tap hint",
                "success_criteria": ["screen changes"],
            }
        ],
    )
    mock_result = MagicMock()
    mock_result.output = plan

    async def _run():
        with patch("game_agent.services.in_game_session_planner.Agent") as agent_cls:
            agent_cls.return_value.run = AsyncMock(return_value=mock_result)
            with patch("game_agent.services.in_game_session_planner.build_llm_model"):
                return await decide_in_game_session_round(
                    llm_cfg=_cfg().llm,
                    deepseek=None,
                    bboxes=[],
                    ocr_summary="guide",
                    screen_analysis=None,
                    round_id=1,
                    elapsed_s=10.0,
                    screen_w=1080,
                    screen_h=1920,
                )

    decision = asyncio.run(_run())
    assert decision.verdict == "continue"
    assert decision.behavior_chain is not None
    assert len(decision.behavior_chain.steps) == 1
