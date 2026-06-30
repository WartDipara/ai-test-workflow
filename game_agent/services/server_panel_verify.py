"""点击区服后：多模态弹窗是否打开探针。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from game_agent.models.server_panel_vision import ServerPanelVisionVerdict
from game_agent.models.settings import LLMSection
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```json"):
        s = s[7:]
    if s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def parse_server_panel_vision(raw: str) -> ServerPanelVisionVerdict:
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        return ServerPanelVisionVerdict(
            reason="vision JSON parse failed",
            parse_failed=True,
        )
    if not isinstance(data, dict):
        return ServerPanelVisionVerdict(
            reason="vision JSON not object",
            parse_failed=True,
        )
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return ServerPanelVisionVerdict(
        passed=bool(data.get("server_list_panel_open", False)),
        same_screen=bool(data.get("same_screen_enter_cta", False)),
        confidence=max(0.0, min(1.0, conf)),
        reason=str(data.get("reason", "") or "")[:500],
    )


def format_panel_vision_summary(verdict: ServerPanelVisionVerdict) -> str:
    return (
        f"[ServerPanelVision] open={verdict.passed} "
        f"same_screen={verdict.same_screen} conf={verdict.confidence:.2f} "
        f"reason={verdict.reason!r}"
    )


async def probe_server_panel_opened(
    *,
    llm_cfg: LLMSection,
    screenshot_path: Path,
    ocr_summary: str = "",
    round_id: int = 0,
    attempt_context=None,
) -> ServerPanelVisionVerdict:
    from game_agent.modules.session_invalidation import capture_session_generation, discard_if_stale

    work_gen = capture_session_generation(attempt_context)
    vision = VisionWorker(llm_cfg, attempt_context=attempt_context)
    raw = await vision.probe_server_panel_opened(
        screenshot_path=screenshot_path,
        ocr_summary=ocr_summary,
        round_id=round_id,
    )
    if discard_if_stale(work_gen, where="server_panel_probe", ctx=attempt_context):
        return ServerPanelVisionVerdict(
            passed=False,
            same_screen=True,
            confidence=0.0,
            reason="stale_session_discard",
        )
    verdict = parse_server_panel_vision(raw)
    logger.info("%s round=%s", format_panel_vision_summary(verdict), round_id)
    return verdict
