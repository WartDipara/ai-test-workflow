"""多模态 → PhaseSpec 解析。"""

from __future__ import annotations

import json
import logging

from game_agent.models.phase_template import CompletionRule, PhaseSpec
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


def parse_phase_spec_raw(raw: str) -> PhaseSpec | None:
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        logger.warning("[PhasePlanner] invalid JSON: %s", (raw or "")[:200])
        return None
    if not isinstance(data, dict):
        return None
    complete_raw = data.get("complete") or {}
    if isinstance(complete_raw, dict):
        complete = CompletionRule(
            kind=str(complete_raw.get("kind", "fingerprint_change") or "fingerprint_change"),
            hint=str(complete_raw.get("hint", "") or ""),
        )
    else:
        complete = CompletionRule()
    try:
        wait_s = float(data.get("wait_s", 2.0) or 2.0)
    except (TypeError, ValueError):
        wait_s = 2.0
    try:
        confidence = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    action = str(data.get("action", "none") or "none").strip().lower()
    if action not in ("tap_xy", "wait", "press_back", "dismiss_blank", "none"):
        action = "none"
    try:
        x = int(data.get("x", 0) or 0)
        y = int(data.get("y", 0) or 0)
    except (TypeError, ValueError):
        x, y = 0, 0
    return PhaseSpec(
        flow_active=bool(data.get("flow_active", True)),
        phase_id=str(data.get("phase_id", "") or "phase"),
        phase_label=str(data.get("phase_label", "") or ""),
        action=action,  # type: ignore[arg-type]
        x=x,
        y=y,
        wait_s=max(0.5, min(wait_s, 8.0)),
        target_text=str(data.get("target_text", "") or "")[:80],
        reason=str(data.get("reason", "") or "")[:500],
        complete=complete,
        confidence=max(0.0, min(confidence, 1.0)),
    )


async def plan_phase_spec(
    vision: VisionWorker,
    *,
    screenshot_path,
    ocr_summary: str,
    completed_phases_summary: str = "",
    prior_phase_summary: str = "",
    stall_hint: str = "",
    login_done: bool = False,
    enter_tapped_count: int = 0,
    round_id: int = 0,
) -> PhaseSpec | None:
    raw = await vision.plan_phase_spec(
        screenshot_path=screenshot_path,
        ocr_summary=ocr_summary,
        completed_phases_summary=completed_phases_summary,
        prior_phase_summary=prior_phase_summary,
        stall_hint=stall_hint,
        login_done=login_done,
        enter_tapped_count=enter_tapped_count,
        round_id=round_id,
    )
    return parse_phase_spec_raw(raw)
