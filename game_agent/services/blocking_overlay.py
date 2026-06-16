"""阻塞弹窗检测与 dismiss 坐标解析（L0 OCR → L2 Interpreter → L3 启发）。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.models.server_connectivity_probe import ServerConnectivityProbe
from game_agent.models.settings import LLMSection
from game_agent.services.node_verifier import NodeVerifyResult, verify_stage_exit
from game_agent.services.screen_interpreter import interpret_launch_screen
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)

_OVERLAY_OCR_RE = re.compile(
    r"Notice|日常通知|公告|announcement|点击空白|点击.*关闭|不再提示|活动",
    re.IGNORECASE,
)
_PROBE_BLOCKING_REASON_RE = re.compile(
    r"modal|notice|通知|遮挡|covering|overlay|popup|弹窗|公告",
    re.IGNORECASE,
)
_DISMISS_BUTTON_RE = re.compile(
    r"^(关闭|关\s*闭|确定|确认|我知道了|我已知晓|OK|Close|Confirm|×|X)$",
    re.IGNORECASE,
)
_DAILY_NOTICE_RE = re.compile(r"日常通知", re.IGNORECASE)

OVERLAY_DISMISS_FOCUS = (
    "blocking overlay: dismiss before server/login/enter; "
    "if 点击空白处关闭, tap blank area outside the panel, not on Start Game"
)

MAX_OVERLAY_DISMISS_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class OverlayDetectResult:
    suspected: bool
    source: str
    hint: str


@dataclass(frozen=True, slots=True)
class OverlayDismissPlan:
    x: int
    y: int
    method: str
    reason: str


def ocr_indicates_blocking_overlay(merged: str) -> bool:
    return bool(_OVERLAY_OCR_RE.search(merged or ""))


def probe_indicates_blocking_overlay(probe: ServerConnectivityProbe | None) -> bool:
    if probe is None:
        return False
    if probe.blocking_overlay or probe.recommendation == "dismiss_overlay":
        return True
    if probe.server_slot_status == "not_visible" and _PROBE_BLOCKING_REASON_RE.search(
        probe.reason or ""
    ):
        return True
    return False


def detect_blocking_overlay(
    *,
    ocr_summary: str,
    bboxes: list[OcrBbox] | None = None,
    facts: LaunchFacts | None = None,
    probe: ServerConnectivityProbe | None = None,
) -> OverlayDetectResult:
    merged = ocr_summary or ""
    if facts is not None and facts.announcement_overlay:
        return OverlayDetectResult(
            suspected=True,
            source="facts",
            hint="announcement_overlay in LaunchFacts",
        )
    if probe_indicates_blocking_overlay(probe):
        reason = (probe.reason if probe else "")[:120]
        return OverlayDetectResult(
            suspected=True,
            source="probe",
            hint=f"ServerProbe blocking: {reason}",
        )
    if ocr_indicates_blocking_overlay(merged):
        return OverlayDetectResult(
            suspected=True,
            source="ocr",
            hint="OCR overlay keywords",
        )
    if bboxes:
        for bbox in bboxes:
            if _OVERLAY_OCR_RE.search(bbox.text):
                return OverlayDetectResult(
                    suspected=True,
                    source="ocr",
                    hint=f"bbox={bbox.text[:40]!r}",
                )
    return OverlayDetectResult(suspected=False, source="", hint="")


def _find_dismiss_button_bbox(bboxes: list[OcrBbox]) -> OcrBbox | None:
    for bbox in bboxes:
        text = bbox.text.strip()
        if _DISMISS_BUTTON_RE.search(text):
            return bbox
    return None


def _find_daily_notice_bbox(bboxes: list[OcrBbox]) -> OcrBbox | None:
    for bbox in bboxes:
        if _DAILY_NOTICE_RE.search(bbox.text):
            return bbox
    return None


def blank_area_tap_xy(
    screen_w: int,
    screen_h: int,
    *,
    modal_bbox_hint: OcrBbox | None = None,
    enter_cta_xy: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """底部空白区 tap；避开 Start Game CTA。"""
    if modal_bbox_hint is not None:
        y = min(screen_h - 1, modal_bbox_hint.y2 + 100)
        x = screen_w // 2
    else:
        x = screen_w // 2
        y = int(screen_h * 0.88)

    if enter_cta_xy is not None:
        ex, ey = enter_cta_xy
        if abs(y - ey) < 150 and abs(x - ex) < int(screen_w * 0.25):
            y = max(0, ey - 200)
    return x, y


def _plan_from_probe(probe: ServerConnectivityProbe) -> OverlayDismissPlan | None:
    if probe.dismiss_tap_x > 0 and probe.dismiss_tap_y > 0:
        return OverlayDismissPlan(
            x=probe.dismiss_tap_x,
            y=probe.dismiss_tap_y,
            method="probe",
            reason=probe.reason[:120] or "probe dismiss_tap",
        )
    return None


def _plan_from_facts(facts: LaunchFacts | None) -> OverlayDismissPlan | None:
    if facts is None or facts.announcement_dismiss_xy is None:
        return None
    x, y = facts.announcement_dismiss_xy
    if x > 0 and y > 0:
        return OverlayDismissPlan(
            x=x,
            y=y,
            method="facts",
            reason="announcement_dismiss_xy from classify",
        )
    return None


async def resolve_dismiss_target(
    *,
    llm_cfg: LLMSection | None,
    screenshot_path: Path,
    ocr_summary: str,
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
    facts: LaunchFacts | None = None,
    probe: ServerConnectivityProbe | None = None,
    round_id: int = 0,
) -> OverlayDismissPlan | None:
    """解析 dismiss 坐标：probe → facts → interpreter → OCR 按钮 → 空白启发。"""
    from_probe = _plan_from_probe(probe) if probe else None
    if from_probe is not None:
        return from_probe

    from_facts = _plan_from_facts(facts)
    if from_facts is not None:
        return from_facts

    if llm_cfg is not None:
        interp = await interpret_launch_screen(
            llm_cfg=llm_cfg,
            screenshot_path=screenshot_path,
            ocr_summary=ocr_summary,
            focus=OVERLAY_DISMISS_FOCUS,
            round_id=round_id,
        )
        tap = interp.tap_target
        if tap is not None and tap.x > 0 and tap.y > 0:
            return OverlayDismissPlan(
                x=tap.x,
                y=tap.y,
                method="interpreter",
                reason=interp.reason[:120] or f"interpreter stage={interp.stage}",
            )

    btn = _find_dismiss_button_bbox(bboxes)
    if btn is not None:
        return OverlayDismissPlan(
            x=btn.cx,
            y=btn.cy,
            method="ocr_button",
            reason=f"OCR dismiss button {btn.text[:30]!r}",
        )

    notice_bbox = _find_daily_notice_bbox(bboxes)
    enter_xy = facts.enter_cta_xy if facts else None
    bx, by = blank_area_tap_xy(
        screen_w,
        screen_h,
        modal_bbox_hint=notice_bbox,
        enter_cta_xy=enter_xy,
    )
    return OverlayDismissPlan(
        x=bx,
        y=by,
        method="blank_heuristic",
        reason="blank area below modal / bottom center",
    )


def verify_overlay_dismissed(ocr_before: str, ocr_after: str) -> NodeVerifyResult:
    return verify_stage_exit(
        ocr_before=ocr_before,
        ocr_after=ocr_after,
        expected_stage="announcement",
        completion_signals=["Start Game", "踏入", "进入游戏", "Enter Game"],
    )


def overlay_still_visible(ocr_summary: str, bboxes: list[OcrBbox] | None = None) -> bool:
    if ocr_indicates_blocking_overlay(ocr_summary):
        return True
    if bboxes:
        return any(_OVERLAY_OCR_RE.search(b.text) for b in bboxes)
    return False
