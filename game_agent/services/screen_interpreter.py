"""同步/异步 Launch 屏幕多模态理解。"""

from __future__ import annotations

import logging
from pathlib import Path

from game_agent.models.screen_interpretation import (
    ScreenInterpretation,
    build_interpretation_prompt,
    parse_interpretation_json,
)
from game_agent.models.settings import LLMSection
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)


async def interpret_launch_screen(
    *,
    llm_cfg: LLMSection | None,
    screenshot_path: Path,
    ocr_summary: str,
    focus: str = "",
    round_id: int = 0,
) -> ScreenInterpretation:
    if llm_cfg is None:
        return ScreenInterpretation(reason="llm_multimodal not configured")
    prompt = build_interpretation_prompt(ocr_summary=ocr_summary, focus=focus)
    vision = VisionWorker(llm_cfg)
    try:
        raw = await vision.interpret_launch_screen(
            screenshot_path=screenshot_path,
            prompt=prompt,
            round_id=round_id,
        )
    except Exception as e:
        logger.warning("[ScreenInterpreter] failed: %s", e)
        return ScreenInterpretation(reason=str(e)[:200])
    interp = parse_interpretation_json(raw)
    if not interp.reason and raw:
        interp = interp.model_copy(update={"reason": "parsed from vision"})
    return interp
