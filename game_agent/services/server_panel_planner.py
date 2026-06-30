"""区服列表面板：OCR 候选交给主脑（纯文本 llm）选关闭或选服。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent

from game_agent.models.settings import DeepSeekSection, LLMSection
from game_agent.services.behavior_chain import strip_json_fence
from game_agent.services.enter_gate_planner import format_ocr_candidates
from game_agent.services.llm_service import build_llm_model
from game_agent.services.server_selector_check import (
    _MODAL_TITLE_TEXT,
    _MODAL_ZONE_RE,
    has_strong_modal_evidence,
)
from game_agent.utils.ocr_util import OcrBbox
from game_agent.i18n import Concept, compile_lexicon_pattern

logger = logging.getLogger(__name__)

_SERVER_NAME_RE = re.compile(
    r"^[\u4e00-\u9fff]{2,8}$",
)
_CLOSE_HINT_RE = compile_lexicon_pattern(Concept.DISMISS_CLOSE, Concept.CANCEL)


class ServerPanelTapPlan(BaseModel):
    action: Literal["tap_xy", "none"] = "none"
    x: int = 0
    y: int = 0
    target_text: str = ""
    intent: Literal["close_panel", "pick_server", "none"] = "none"
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ServerPanelTapDecision:
    x: int
    y: int
    target_text: str
    intent: str
    reason: str
    source: str


def server_panel_still_open(bboxes: list[OcrBbox], *, enter: OcrBbox | None = None) -> bool:
    if enter is not None:
        return has_strong_modal_evidence(bboxes, enter)
    merged = " ".join(b.text for b in bboxes if b.text.strip())
    if _MODAL_TITLE_TEXT.search(merged):
        return True
    return bool(_MODAL_ZONE_RE.search(merged))


def decide_server_panel_tap_heuristic(
    bboxes: list[OcrBbox],
    *,
    screen_w: int = 0,
    screen_h: int = 0,
    prefer_close: bool = True,
) -> ServerPanelTapDecision | None:
    """启发式：优先关面板（关闭文案 / 右上空白），否则点第一个区服名。"""
    close_candidates: list[OcrBbox] = []
    server_candidates: list[OcrBbox] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text:
            continue
        if _CLOSE_HINT_RE.search(text):
            close_candidates.append(bbox)
        elif _SERVER_NAME_RE.match(text) and not _MODAL_TITLE_TEXT.search(text):
            if "区" not in text and "推荐" not in text:
                server_candidates.append(bbox)

    if prefer_close and close_candidates:
        pick = min(close_candidates, key=lambda b: b.y1)
        return ServerPanelTapDecision(
            x=pick.cx,
            y=pick.cy,
            target_text=pick.text.strip(),
            intent="close_panel",
            reason="heuristic:close_text",
            source="heuristic",
        )

    if prefer_close and screen_w > 0 and screen_h > 0:
        rx = int(screen_w * 0.88)
        ry = int(screen_h * 0.18)
        return ServerPanelTapDecision(
            x=rx,
            y=ry,
            target_text="",
            intent="close_panel",
            reason="heuristic:dialog_top_right",
            source="heuristic",
        )

    if server_candidates:
        pick = server_candidates[0]
        return ServerPanelTapDecision(
            x=pick.cx,
            y=pick.cy,
            target_text=pick.text.strip(),
            intent="pick_server",
            reason="heuristic:first_server_name",
            source="heuristic",
        )
    return None


def _snap_to_bbox(
    plan: ServerPanelTapPlan,
    bboxes: list[OcrBbox],
) -> ServerPanelTapDecision | None:
    if plan.action != "tap_xy" or plan.x <= 0 or plan.y <= 0:
        return None
    needle = (plan.target_text or "").strip()
    if needle:
        for bbox in bboxes:
            text = (bbox.text or "").strip()
            if needle in text or text in needle:
                return ServerPanelTapDecision(
                    x=bbox.cx,
                    y=bbox.cy,
                    target_text=text,
                    intent=plan.intent or "close_panel",
                    reason=plan.reason or "brain:matched_target",
                    source="brain",
                )
    return ServerPanelTapDecision(
        x=plan.x,
        y=plan.y,
        target_text=needle,
        intent=plan.intent or "close_panel",
        reason=plan.reason or "brain:raw_coords",
        source="brain",
    )


async def decide_server_panel_tap(
    *,
    llm_cfg: LLMSection | None,
    bboxes: list[OcrBbox],
    ocr_summary: str = "",
    screen_w: int = 0,
    screen_h: int = 0,
    deepseek: DeepSeekSection | None = None,
    prefer_close: bool = True,
) -> ServerPanelTapDecision | None:
    heuristic = decide_server_panel_tap_heuristic(
        bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
        prefer_close=prefer_close,
    )
    if llm_cfg is None:
        return heuristic

    goal = (
        "close the server list dialog and return to enter-game gate"
        if prefer_close
        else "pick any playable server row in the open list"
    )
    prompt = f"""
You are the main brain for an Android game server selection overlay.
The server list panel is OPEN. Pick exactly ONE OCR row to tap.

Goal: {goal}
Screen: {screen_w}x{screen_h}

OCR candidates (JSON):
{format_ocr_candidates(bboxes)}

OCR summary:
{ocr_summary[:2000]}

Rules:
- To close: tap Close/X text if present, else top-right of the dialog (~88% width, ~18% height).
- To pick server: tap any visible server name row (e.g. 迢迢暗度), NOT zone tabs like 五十区 unless no server rows.
- Do NOT tap 进入游戏, privacy, or health footer text.

JSON only:
{{
  "action": "tap_xy | none",
  "x": 0,
  "y": 0,
  "target_text": "OCR text chosen",
  "intent": "close_panel | pick_server | none",
  "reason": "one sentence"
}}
"""
    try:
        model = build_llm_model(llm_cfg, deepseek=deepseek)
        agent = Agent(model, output_type=ServerPanelTapPlan)
        result = await agent.run(prompt)
        plan = result.output
        if plan.action == "tap_xy" and plan.x > 0 and plan.y > 0:
            snapped = _snap_to_bbox(plan, bboxes)
            if snapped is not None:
                logger.info(
                    "[ServerPanel] brain_pick intent=%s label=%r xy=(%d,%d)",
                    snapped.intent,
                    snapped.target_text[:60],
                    snapped.x,
                    snapped.y,
                )
                return snapped
    except Exception:
        logger.exception("[ServerPanel] main brain tap decision failed")

    if heuristic is not None:
        logger.info(
            "[ServerPanel] fallback_heuristic intent=%s xy=(%d,%d)",
            heuristic.intent,
            heuristic.x,
            heuristic.y,
        )
    return heuristic
