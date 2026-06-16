from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from game_agent.graphs.vision_enrichment import VisionEnrichmentQueue
from game_agent.models.settings import LLMSection


def test_vision_queue_shutdown_awaits_cancelled_task() -> None:
    async def _run() -> None:
        cfg = LLMSection(
            base_url="https://example.com",
            api_key="k",
            model_name="m",
        )
        queue = VisionEnrichmentQueue(llm_cfg=cfg)

        async def slow_vision(*_a, **_k):
            await asyncio.sleep(10)
            return "{}"

        with patch(
            "game_agent.graphs.vision_enrichment.VisionWorker.analyze_game_state",
            new=AsyncMock(side_effect=slow_vision),
        ):
            queue.submit(Path("x.png"), "ocr")
            await asyncio.sleep(0.05)
            await queue.shutdown()

        assert queue._task is None

    asyncio.run(_run())
