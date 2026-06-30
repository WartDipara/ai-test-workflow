"""in_game_screen_analyze 单元测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.launch_graph_state import empty_launch_graph_state
from game_agent.models.settings import AppConfig
from game_agent.services.in_game_screen_analyze import run_in_game_screen_analyze_on_capture


def _cfg() -> AppConfig:
    return AppConfig.model_validate(
        {
            "llm": {"base_url": "http://x", "api_key": "k", "model_name": "m"},
            "llm_multimodal": {"base_url": "http://x", "api_key": "k", "model_name": "v"},
            "game": {"package_name": "com.test.game"},
        }
    )


def test_analyze_writes_state_without_verdict(tmp_path) -> None:
    shot = tmp_path / "s.png"
    shot.write_bytes(b"x")
    state = empty_launch_graph_state()
    analysis = InGameScreenAnalysis(
        forced_guidance_present=True,
        guidance_signals=["finger_hint"],
        ui_stage="tutorial",
        observations="Finger pointer on bottom-right CTA.",
        confidence=0.88,
    )
    vision = MagicMock()
    vision.analyze_in_game_screen = AsyncMock(return_value=analysis)

    async def _run():
        with patch(
            "game_agent.services.in_game_screen_analyze.VisionWorker",
            return_value=vision,
        ):
            return await run_in_game_screen_analyze_on_capture(
                shot_path=shot,
                ocr_summary="guide text",
                cfg=_cfg(),
                state=state,
                round_id=1,
            )

    result = asyncio.run(_run())
    assert result.analysis is not None
    assert result.analysis.forced_guidance_present is True
    assert "verdict" not in state["last_in_game_screen_analysis"]
    assert state["last_in_game_screen_analysis"]["ui_stage"] == "tutorial"


def test_analyze_cache_by_shot_hash(tmp_path) -> None:
    shot = tmp_path / "s.png"
    shot.write_bytes(b"cached")
    state = empty_launch_graph_state()
    state["in_game_analyze_cache_hash"] = "abc"
    state["last_in_game_screen_analysis"] = InGameScreenAnalysis(
        ui_stage="hud",
        confidence=0.9,
    ).model_dump()

    async def _run():
        with patch("game_agent.services.in_game_screen_analyze.VisionWorker") as mock_cls:
            result = await run_in_game_screen_analyze_on_capture(
                shot_path=shot,
                ocr_summary="hud",
                cfg=_cfg(),
                state=state,
                round_id=2,
                shot_hash="abc",
            )
            mock_cls.assert_not_called()
            return result
    result = asyncio.run(_run())
    assert result.message == "cached"
