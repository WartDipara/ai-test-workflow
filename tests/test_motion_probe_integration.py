"""主脑局内规划应包含 motion/spatial 上下文。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from game_agent.models.in_game_session_decision import InGameSessionPlan
from game_agent.services.in_game_session_planner import decide_in_game_session_round


def test_session_planner_prompt_includes_motion_context() -> None:
    plan = InGameSessionPlan(
        verdict="wait",
        confidence=0.8,
        reason="loading",
        wait_s=2.0,
    )
    mock_result = MagicMock()
    mock_result.output = plan
    captured: dict[str, str] = {}

    async def _capture_run(prompt: str):
        captured["prompt"] = prompt
        return mock_result

    async def _run() -> str:
        with patch("game_agent.services.in_game_session_planner.Agent") as agent_cls:
            agent_cls.return_value.run = AsyncMock(side_effect=_capture_run)
            with patch("game_agent.services.in_game_session_planner.build_llm_model"):
                from game_agent.models.settings import LLMSection

                await decide_in_game_session_round(
                    llm_cfg=LLMSection(
                        base_url="http://x",
                        api_key="k",
                        model_name="m",
                    ),
                    deepseek=None,
                    bboxes=[],
                    ocr_summary="(100,200) OK",
                    screen_analysis=None,
                    motion_summary="pulsing_fixed: P1 center=(981,1612)",
                    spatial_hints="tutorial_candidates: rank=1",
                    round_id=1,
                    elapsed_s=30.0,
                    screen_w=1080,
                    screen_h=2400,
                )
        return captured.get("prompt", "")

    prompt = asyncio.run(_run())
    assert "pulsing_fixed" in prompt
    assert "tutorial_candidates" in prompt
