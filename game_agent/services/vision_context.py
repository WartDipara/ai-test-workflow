from __future__ import annotations

import logging
from pathlib import Path

from game_agent.models.settings import LLMSection
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)


async def summarize_monitor_screenshots(
    multimodal: LLMSection,
    paths: list[Path],
    *,
    max_images: int = 3,
) -> str:
    """
    Use llm_multimodal (VisionWorker) to turn monitor screenshots into text
    for the main LLM — no tool_choice / structured output on the vision model.
    """
    shots = [p for p in paths if p.is_file()][-max_images:]
    if not shots:
        return ""

    worker = VisionWorker(multimodal)
    blocks: list[str] = ["## Screen monitor captures (vision model, text for main LLM)"]
    for shot in shots:
        try:
            summary = await worker.analyze_game_state(
                screenshot_path=shot,
                ocr_summary="(monitor screenshot; OCR not bundled)",
            )
            blocks.append(f"### {shot.name}\n{summary}")
        except Exception as e:
            logger.warning("Screenshot multimodal summary failed %s: %s", shot.name, e)
            blocks.append(f"### {shot.name}\n(vision summary failed: {e})")
    return "\n".join(blocks) + "\n"
