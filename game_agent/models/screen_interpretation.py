"""多模态屏幕理解：Launch 流程统一 JSON schema。"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from game_agent.i18n import Concept, compile_lexicon_pattern


class TapTarget(BaseModel):
    x: int = 0
    y: int = 0
    label: str = ""


class ScreenInterpretation(BaseModel):
    """ScreenInterpreter 输出；坐标为设备触控像素（与 OCR 一致）。"""

    stage: str = "unknown"
    blocking: bool = False
    tap_target: TapTarget | None = None
    completion_signals: list[str] = Field(default_factory=list)
    reason: str = ""


def _strip_json_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def parse_interpretation_json(raw: str) -> ScreenInterpretation:
    text = _strip_json_fence(raw)
    if not text:
        return ScreenInterpretation(reason="empty response")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ScreenInterpretation(reason="invalid JSON")
    if not isinstance(data, dict):
        return ScreenInterpretation(reason="JSON not object")
    return interpretation_from_dict(data)


def interpretation_from_dict(data: dict[str, Any]) -> ScreenInterpretation:
    stage = str(data.get("stage", "unknown") or "unknown").strip().lower()
    blocking = bool(data.get("blocking", False))
    reason = str(data.get("reason", "") or "")[:300]

    tap_raw = data.get("tap_target")
    tap_target: TapTarget | None = None
    if isinstance(tap_raw, dict):
        try:
            x = int(tap_raw.get("x", 0))
            y = int(tap_raw.get("y", 0))
            label = str(tap_raw.get("label", "") or "")[:80]
            if x > 0 and y > 0:
                tap_target = TapTarget(x=x, y=y, label=label)
        except (TypeError, ValueError):
            tap_target = None

    signals_raw = data.get("completion_signals")
    completion_signals: list[str] = []
    if isinstance(signals_raw, list):
        completion_signals = [str(s)[:60] for s in signals_raw if s][:8]

    return ScreenInterpretation(
        stage=stage,
        blocking=blocking,
        tap_target=tap_target,
        completion_signals=completion_signals,
        reason=reason,
    )


_INTERPRETATION_JSON_SCHEMA = """
{
  "stage": "sub_account_select | login | server_select | resource_download | character_creation | announcement | privacy | in_game | unknown",
  "blocking": bool,
  "tap_target": {"x": int, "y": int, "label": "short label"} or null,
  "completion_signals": ["keywords that mean this stage is done after action"],
  "reason": "brief explanation"
}
"""


def build_interpretation_prompt(*, ocr_summary: str, focus: str = "") -> str:
    focus_block = f"\nFocus: {focus}\n" if focus else ""
    return f"""
You interpret a mobile game launch screen for automation. Use screenshot + OCR.
OCR lines are (x,y) text in device touch coordinates — tap_target must use same space.
{focus_block}
OCR:
{ocr_summary}

Rules:
- sub_account_select: sub-account / alt character picker (小号, 子账号, Sub-account). tap_target = existing account row to enter (NOT create/purchase buttons).
- login: account/password form blocking entry.
- server_select: server list or enter-game button on pre-entry screen.
- announcement: event/notice popup blocking; tap_target = close/dismiss (X, 关闭, 今日不再).
  If UI says 点击空白处关闭, tap blank area outside the panel (below modal), NOT on Start Game.
- character_creation: class/name/avatar creation UI blocking.
- resource_download: asset download progress.
- blocking=true when user must act before proceeding; false when clear to route elsewhere.
- If unsure, stage=unknown, blocking=false.

Return valid JSON only (no markdown):
{_INTERPRETATION_JSON_SCHEMA}
"""


_SUB_ACCOUNT_STAGE_RE = compile_lexicon_pattern(Concept.SUB_ACCOUNT)
_LOGIN_STAGE_RE = re.compile(
    compile_lexicon_pattern(Concept.LOGIN).pattern + r"|login_form",
    re.IGNORECASE,
)
_ANNOUNCEMENT_STAGE_RE = compile_lexicon_pattern(Concept.ANNOUNCEMENT, Concept.OVERLAY)
_CHARACTER_STAGE_RE = re.compile(
    compile_lexicon_pattern(Concept.CHARACTER_CREATION).pattern + r"|character_creation",
    re.IGNORECASE,
)
