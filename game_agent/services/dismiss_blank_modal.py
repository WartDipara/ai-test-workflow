"""Dismiss modals that instruct tap-blank-to-close (OCR + geometry heuristic)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from game_agent.models.phase_template import PhaseSpec
from game_agent.services.blocking_overlay import blank_area_tap_xy
from game_agent.utils.ocr_util import OcrBbox

_BLANK_DISMISS_HINT_RE = re.compile(
    r"点击空白|tap\s*blank|click\s*blank|click\s*empty|tap\s*empty|"
    r"点击.*关闭|tap\s*to\s*close|press\s*anywhere",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DismissBlankPlan:
    x: int
    y: int
    method: str
    hint_text: str
    reason: str


def ocr_indicates_blank_dismiss(ocr_summary: str) -> bool:
    return bool(_BLANK_DISMISS_HINT_RE.search(ocr_summary or ""))


def find_blank_dismiss_hint_bbox(bboxes: list[OcrBbox]) -> OcrBbox | None:
    for bbox in bboxes:
        if _BLANK_DISMISS_HINT_RE.search(bbox.text or ""):
            return bbox
    return None


def plan_blank_area_dismiss(
    *,
    ocr_summary: str,
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
    enter_cta_xy: tuple[int, int] | None = None,
) -> DismissBlankPlan | None:
    """Plan a tap outside the modal when blank-dismiss copy is visible."""
    hint_bbox = find_blank_dismiss_hint_bbox(bboxes)
    if hint_bbox is None and not ocr_indicates_blank_dismiss(ocr_summary):
        return None

    hint_text = hint_bbox.text.strip() if hint_bbox is not None else ""
    if not hint_text:
        match = _BLANK_DISMISS_HINT_RE.search(ocr_summary or "")
        hint_text = match.group(0) if match else "blank dismiss"

    x, y = blank_area_tap_xy(
        screen_w,
        screen_h,
        modal_bbox_hint=hint_bbox,
        enter_cta_xy=enter_cta_xy,
    )
    method = "below_modal" if hint_bbox is not None else "bottom_heuristic"
    return DismissBlankPlan(
        x=x,
        y=y,
        method=method,
        hint_text=hint_text[:80],
        reason=f"blank dismiss ({method}) for {hint_text[:40]!r}",
    )


def execute_dismiss_blank_modal(
    spec: PhaseSpec,
    *,
    adb,
    sw: int,
    sh: int,
    ocr_summary: str,
    bboxes: list[OcrBbox],
    enter_cta_xy: tuple[int, int] | None = None,
) -> tuple[str, bool]:
    """Run blank-dismiss tool; falls back to spec x,y if no hint detected."""
    plan = plan_blank_area_dismiss(
        ocr_summary=ocr_summary,
        bboxes=bboxes,
        screen_w=sw,
        screen_h=sh,
        enter_cta_xy=enter_cta_xy,
    )
    if plan is not None:
        return adb.tap(plan.x, plan.y, width=sw, height=sh), True
    if spec.x > 0 and spec.y > 0:
        return adb.tap(spec.x, spec.y, width=sw, height=sh), True
    return "dismiss_blank: no hint and no fallback coordinates", False
