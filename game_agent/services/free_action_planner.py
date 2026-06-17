"""登录后 free 节点：基于 OCR 启发式 + 多模态规划单步动作。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from game_agent.utils.ocr_util import OcrBbox
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)

FreeActionType = Literal["tap_text", "tap_xy", "press_back", "wait", "none"]

_ENTER_WORLD_RE = re.compile(
    r"进入世界|进入游戏|开始游戏|踏入|Enter\s*World|Start\s*Game|进入",
    re.IGNORECASE,
)
_CREATE_ROLE_RE = re.compile(
    r"创建角色|新建角色|Click\s*to\s*Create|Create\s*Role|选择职业|创角",
    re.IGNORECASE,
)
_CONFIRM_RE = re.compile(
    r"^(确定|确认|继续|OK|Continue|Confirm|Agree)$",
    re.IGNORECASE,
)
_SKIP_RE = re.compile(r"跳过|Skip", re.IGNORECASE)
_SELECT_CHAR_RE = re.compile(r"选择角色|已有角色|LV\.|等级", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FreeActionPlan:
    action: FreeActionType
    x: int = 0
    y: int = 0
    target_text: str = ""
    wait_s: float = 1.5
    reason: str = ""
    stage: str = ""

    def signature(self) -> str:
        return f"{self.action}:{self.x}:{self.y}:{self.target_text}:{self.wait_s:.1f}"


def _bbox_for_pattern(bboxes: list[OcrBbox], pattern: re.Pattern[str]) -> OcrBbox | None:
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text:
            continue
        if pattern.search(text):
            candidates.append((bbox.cy, bbox))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def decide_free_action_heuristic(
    bboxes: list[OcrBbox],
    *,
    ocr_summary: str = "",
) -> FreeActionPlan | None:
    """OCR 启发式：优先进入世界 / 选角 / 创建 / 确认 / 跳过。"""
    merged = ocr_summary or " ".join(b.text for b in bboxes)

    for pattern, label in (
        (_ENTER_WORLD_RE, "enter_world"),
        (_CREATE_ROLE_RE, "create_role"),
        (_SKIP_RE, "skip"),
        (_CONFIRM_RE, "confirm"),
    ):
        bbox = _bbox_for_pattern(bboxes, pattern)
        if bbox is not None:
            return FreeActionPlan(
                action="tap_xy",
                x=bbox.cx,
                y=bbox.cy,
                target_text=bbox.text.strip(),
                reason=f"heuristic:{label}",
                stage=label,
            )

    if _SELECT_CHAR_RE.search(merged):
        bbox = _bbox_for_pattern(bboxes, _ENTER_WORLD_RE)
        if bbox is not None:
            return FreeActionPlan(
                action="tap_xy",
                x=bbox.cx,
                y=bbox.cy,
                target_text=bbox.text.strip(),
                reason="heuristic:select_existing_char",
                stage="character_select",
            )

    return None


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```json"):
        s = s[7:]
    if s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def _parse_free_action_json(raw: str) -> FreeActionPlan | None:
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action", "") or "none").strip().lower()
    if action not in ("tap_text", "tap_xy", "press_back", "wait", "none"):
        action = "none"
    try:
        x = int(data.get("x", 0) or 0)
        y = int(data.get("y", 0) or 0)
    except (TypeError, ValueError):
        x, y = 0, 0
    try:
        wait_s = float(data.get("wait_s", 1.5) or 1.5)
    except (TypeError, ValueError):
        wait_s = 1.5
    wait_s = max(0.5, min(wait_s, 5.0))
    return FreeActionPlan(
        action=action,  # type: ignore[arg-type]
        x=x,
        y=y,
        target_text=str(data.get("target_text", "") or "")[:80],
        wait_s=wait_s,
        reason=str(data.get("reason", "") or "")[:300],
        stage=str(data.get("stage", "") or "")[:60],
    )


async def decide_free_action_vision(
    vision: VisionWorker,
    *,
    screenshot_path: Path,
    ocr_summary: str,
    round_id: int,
    prior_action_signature: str = "",
) -> FreeActionPlan | None:
    """多模态规划单步动作（白名单内）。"""
    avoid = ""
    if prior_action_signature:
        avoid = (
            f"\nPrevious action did not change the screen: {prior_action_signature}. "
            "Pick a different action (e.g. wait, press_back, or another tap target).\n"
        )
    prompt = f"""
You help automate a mobile game past login into the real in-game scene.
The player may be on character creation, character select, tutorial popup, or loading.

OCR (x,y text):
{ocr_summary}
{avoid}

Pick exactly ONE next action from this whitelist:
- tap_xy: tap coordinates (x,y) for a visible button (Enter World, Create Role, Confirm, Skip, etc.)
- press_back: dismiss blocking overlay if back is safer than random tap
- wait: wait for loading (wait_s 1.5-3.0)
- none: cannot decide safely

Do NOT suggest typing text, uninstall, or system commands.

JSON only (no markdown):
{{
  "action": "tap_xy | press_back | wait | none",
  "x": 0,
  "y": 0,
  "target_text": "button label if any",
  "wait_s": 1.5,
  "stage": "character_creation | character_select | loading | tutorial | unknown",
  "reason": "one sentence"
}}
"""
    raw = await vision.plan_free_step(
        screenshot_path=screenshot_path,
        prompt=prompt,
        round_id=round_id,
    )
    plan = _parse_free_action_json(raw)
    if plan is None:
        logger.warning("[FreeAction] vision JSON parse failed: %s", raw[:200])
    return plan


async def decide_free_action(
    *,
    vision: VisionWorker | None,
    screenshot_path: Path,
    bboxes: list[OcrBbox],
    ocr_summary: str,
    round_id: int,
    prior_action_signature: str = "",
) -> FreeActionPlan:
    heuristic = decide_free_action_heuristic(bboxes, ocr_summary=ocr_summary)
    if heuristic is not None:
        logger.info(
            "[FreeAction] heuristic %s (%s,%s) %s",
            heuristic.reason,
            heuristic.x,
            heuristic.y,
            heuristic.target_text[:40],
        )
        return heuristic

    if vision is not None:
        vision_plan = await decide_free_action_vision(
            vision,
            screenshot_path=screenshot_path,
            ocr_summary=ocr_summary,
            round_id=round_id,
            prior_action_signature=prior_action_signature,
        )
        if vision_plan is not None and vision_plan.action != "none":
            logger.info(
                "[FreeAction] vision %s action=%s (%s,%s)",
                vision_plan.reason[:80],
                vision_plan.action,
                vision_plan.x,
                vision_plan.y,
            )
            return vision_plan

    return FreeActionPlan(
        action="wait",
        wait_s=2.0,
        reason="no heuristic/vision tap; wait for UI",
        stage="unknown",
    )


def compute_progress_fingerprint(
    *,
    current_stage: str,
    ocr_summary: str,
    vision_stage: str = "",
) -> str:
    ocr_head = (ocr_summary or "")[:240]
    return f"{current_stage}|{vision_stage}|{ocr_head}"
