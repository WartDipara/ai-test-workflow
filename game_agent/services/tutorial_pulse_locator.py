"""教程无文字 UI：OpenCV 脉冲精确选点 + 单帧高亮兜底。"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from game_agent.i18n import Concept, compile_lexicon_pattern
from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.motion_probe import MotionProbeResult, MotionRegion
from game_agent.models.tutorial_pulse import TutorialPulsePick
from game_agent.services.tutorial_intent import find_tutorial_anchor_bbox
from game_agent.services.motion_probe import _hsv_white_glow_mask, _hsv_yellow_mask
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)

_CTA_OVERLAP_RE = compile_lexicon_pattern(Concept.SPATIAL_BUTTON, Concept.SKIP)
_BATTLE_CTA_RE = re.compile(r"^战斗$|^battle$", re.IGNORECASE)
_MAX_PULSE_SCREEN_RATIO = 0.22


@dataclass(frozen=True, slots=True)
class TutorialPulseTap:
    x: int
    y: int
    pulse_rank: int
    reason: str
    source: str = "motion_pulse"


def list_pulsing_fixed(motion: MotionProbeResult | None) -> list[MotionRegion]:
    if motion is None:
        return []
    return [r for r in motion.regions if r.kind == "pulsing_fixed"]


def _dist(ax: int, ay: int, bx: int, by: int) -> float:
    return math.hypot(ax - bx, ay - by)


def _bbox_center(bbox: OcrBbox) -> tuple[int, int]:
    return bbox.cx, bbox.cy


def _overlaps_cta(pulse: MotionRegion, bboxes: list[OcrBbox], *, radius_px: int = 90) -> bool:
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text:
            continue
        if _BATTLE_CTA_RE.match(text):
            if _dist(pulse.cx, pulse.cy, bbox.cx, bbox.cy) <= radius_px:
                return True
        if _CTA_OVERLAP_RE.search(text) and _dist(pulse.cx, pulse.cy, bbox.cx, bbox.cy) <= radius_px:
            return True
    return False


def _pulse_too_large(pulse: MotionRegion, screen_w: int, screen_h: int) -> bool:
    if screen_w <= 0 or screen_h <= 0:
        return False
    return (pulse.area / float(screen_w * screen_h)) > _MAX_PULSE_SCREEN_RATIO


def _score_pulse(
    pulse: MotionRegion,
    *,
    rank: int,
    anchor: OcrBbox | None,
    vlm_pick: TutorialPulsePick | None,
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
) -> float | None:
    if _pulse_too_large(pulse, screen_w, screen_h):
        return None
    if _overlaps_cta(pulse, bboxes):
        return None

    if vlm_pick is not None and vlm_pick.reject_ranks and rank in vlm_pick.reject_ranks:
        return None

    score = pulse.score * 10.0 + max(0.0, 3.0 - rank * 0.2)

    if vlm_pick is not None and vlm_pick.chosen_pulse_rank == rank:
        score += 50.0
    if vlm_pick is not None and vlm_pick.preferred_band:
        band = str(pulse.extra.get("band") or "")
        if band == vlm_pick.preferred_band:
            score += 8.0
        elif band and band != vlm_pick.preferred_band:
            score -= 4.0

    if anchor is not None:
        dist = _dist(pulse.cx, pulse.cy, anchor.cx, anchor.cy)
        score += max(0.0, 12.0 - dist / 80.0)

    return score


def pick_tutorial_pulse_tap(
    motion: MotionProbeResult | None,
    *,
    screen_w: int,
    screen_h: int,
    vlm_pick: TutorialPulsePick | None = None,
    ocr_summary: str = "",
    bboxes: list[OcrBbox] | None = None,
) -> TutorialPulseTap | None:
    """在 pulsing_fixed 候选中选教程点击坐标（OpenCV 精确，非 VLM）。"""
    pulses = list_pulsing_fixed(motion)
    if not pulses:
        return None

    boxes = list(bboxes or [])
    anchor = find_tutorial_anchor_bbox(boxes, ocr_summary=ocr_summary)

    ranked: list[tuple[float, int, MotionRegion]] = []
    for rank, pulse in enumerate(pulses, start=1):
        score = _score_pulse(
            pulse,
            rank=rank,
            anchor=anchor,
            vlm_pick=vlm_pick,
            bboxes=boxes,
            screen_w=screen_w,
            screen_h=screen_h,
        )
        if score is not None:
            ranked.append((score, rank, pulse))

    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, best_rank, best = ranked[0]
    reason = f"pulse_rank_{best_rank} score={best_score:.2f}"
    if vlm_pick is not None and vlm_pick.chosen_pulse_rank == best_rank:
        reason = f"vlm_pick_rank_{best_rank}"
    elif anchor is not None:
        reason = f"anchor_nearest_pulse rank={best_rank}"

    return TutorialPulseTap(
        x=best.cx,
        y=best.cy,
        pulse_rank=best_rank,
        reason=reason,
        source="motion_pulse",
    )


def _glow_centroids_from_mask(
    mask: np.ndarray,
    *,
    min_area: int,
    top_k: int = 6,
) -> list[tuple[int, int, int]]:
    """返回 (cx, cy, area)。"""
    blurred = cv2.GaussianBlur(mask, (5, 5), 0)
    _, binary = cv2.threshold(blurred, 40, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binary = cv2.morphologyEx(cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel), cv2.MORPH_CLOSE, kernel)
    n, _, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    out: list[tuple[int, int, int]] = []
    for idx in range(1, n):
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        cx, cy = int(centroids[idx][0]), int(centroids[idx][1])
        out.append((cx, cy, area))
    out.sort(key=lambda item: item[2], reverse=True)
    return out[:top_k]


def detect_static_glow_tap(
    image_path: Path,
    *,
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
    ocr_summary: str = "",
    min_area: int = 200,
) -> TutorialPulseTap | None:
    """连拍无 pulse 时：单帧 HSV 白/黄高亮 + OCR 锚点几何。"""
    if not image_path.is_file():
        return None
    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        return None

    yellow = _hsv_yellow_mask(bgr)
    white = _hsv_white_glow_mask(bgr)
    combined = cv2.max(yellow, white)
    centroids = _glow_centroids_from_mask(combined, min_area=min_area)
    if not centroids:
        return None

    anchor = find_tutorial_anchor_bbox(bboxes, ocr_summary=ocr_summary)
    best: tuple[float, int, int] | None = None
    for cx, cy, area in centroids:
        pseudo = MotionRegion(
            kind="pulsing_fixed",
            cx=cx,
            cy=cy,
            bbox=(cx, cy, 1, 1),
            area=area,
            score=1.0,
        )
        if _pulse_too_large(pseudo, screen_w, screen_h):
            continue
        if _overlaps_cta(pseudo, bboxes):
            continue
        score = float(area)
        if anchor is not None:
            score += max(0.0, 5000.0 - _dist(cx, cy, anchor.cx, anchor.cy) * 3.0)
        if best is None or score > best[0]:
            best = (score, cx, cy)

    if best is None:
        return None
    _, x, y = best
    return TutorialPulseTap(x=x, y=y, pulse_rank=0, reason="static_glow", source="static_glow")


def resolve_tutorial_visual_tap(
    *,
    motion: MotionProbeResult | None,
    screenshot_path: Path | None,
    screen_w: int,
    screen_h: int,
    ocr_summary: str,
    bboxes: list[OcrBbox],
    vlm_pick: TutorialPulsePick | None = None,
    allow_static_glow: bool = True,
) -> TutorialPulseTap | None:
    tap = pick_tutorial_pulse_tap(
        motion,
        screen_w=screen_w,
        screen_h=screen_h,
        vlm_pick=vlm_pick,
        ocr_summary=ocr_summary,
        bboxes=bboxes,
    )
    if tap is not None:
        return tap
    if allow_static_glow and screenshot_path is not None:
        return detect_static_glow_tap(
            screenshot_path,
            bboxes=bboxes,
            screen_w=screen_w,
            screen_h=screen_h,
            ocr_summary=ocr_summary,
        )
    return None


def apply_motion_pulse_to_analysis(
    analysis: InGameScreenAnalysis,
    tap: TutorialPulseTap,
    *,
    vlm_pick: TutorialPulsePick | None = None,
) -> InGameScreenAnalysis:
    """将 OpenCV 脉冲坐标注入 VLM 分析结果。"""
    conf = max(analysis.tap_confidence, 0.55)
    if vlm_pick is not None and vlm_pick.confidence > 0:
        conf = max(conf, vlm_pick.confidence)
    fusion = tap.reason
    if vlm_pick is not None and vlm_pick.reason:
        fusion = f"{tap.reason}; {vlm_pick.reason}"[:300]
    return analysis.model_copy(
        update={
            "forced_guidance_present": True,
            "recommended_action": "tap_xy",
            "tap_x": tap.x,
            "tap_y": tap.y,
            "tap_target_text": "",
            "tap_source": "motion_pulse",
            "tap_confidence": min(1.0, conf),
            "fusion_reason": fusion[:300],
            "ui_stage": analysis.ui_stage if analysis.ui_stage != "unknown" else "tutorial",
            "guidance_signals": list(
                dict.fromkeys([*(analysis.guidance_signals or []), "pulsing_cta", "finger_hint"])
            ),
        }
    )
