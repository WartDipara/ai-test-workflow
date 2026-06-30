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
    attempt_context=None,
) -> ScreenInterpretation:
    if llm_cfg is None:
        return ScreenInterpretation(reason="llm_multimodal not configured")
    from game_agent.modules.session_invalidation import capture_session_generation, discard_if_stale

    work_gen = capture_session_generation(attempt_context)
    prompt = build_interpretation_prompt(ocr_summary=ocr_summary, focus=focus)
    vision = VisionWorker(llm_cfg, attempt_context=attempt_context)
    try:
        raw = await vision.interpret_launch_screen(
            screenshot_path=screenshot_path,
            prompt=prompt,
            round_id=round_id,
        )
    except Exception as e:
        logger.warning("[ScreenInterpreter] failed: %s", e)
        return ScreenInterpretation(reason=str(e)[:200])
    if discard_if_stale(work_gen, where="interpret_launch_screen", ctx=attempt_context):
        return ScreenInterpretation(reason="stale_session_discard")
    interp = parse_interpretation_json(raw)
    if not interp.reason and raw:
        interp = interp.model_copy(update={"reason": "parsed from vision"})
    return interp
