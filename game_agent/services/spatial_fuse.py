"""OCR bbox 与 motion_probe 热点空间融合。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from game_agent.models.motion_probe import MotionProbeResult, MotionProbeSection, MotionRegion
from game_agent.utils.ocr_util import OcrBbox

_BUTTON_RE = re.compile(
    r"^(确定|确认|继续|关闭|取消|领取|前往|挑战|开始|跳过|OK|Continue|Close|Skip)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SpatialFuseResult:
    hints_text: str
    top_tap_candidate: tuple[int, int] | None
    top_tap_reason: str
    top_tap_score: float


def _dist(ax: int, ay: int, bx: int, by: int) -> float:
    return math.hypot(ax - bx, ay - by)


def spatial_fuse(
    bboxes: list[OcrBbox],
    motion: MotionProbeResult | None,
    *,
    motion_cfg: MotionProbeSection | None = None,
) -> SpatialFuseResult:
    cfg = motion_cfg or MotionProbeSection()
    radius = float(cfg.ocr_fuse_radius_px)

    if motion is None or not motion.regions:
        return SpatialFuseResult(
            hints_text="spatial_fuse: no motion regions",
            top_tap_candidate=None,
            top_tap_reason="no_motion",
            top_tap_score=0.0,
        )

    pulsing = [r for r in motion.regions if r.kind == "pulsing_fixed"]
    moving = [r for r in motion.regions if r.kind == "moving_sprite"]
    swipe_hints = [r for r in motion.regions if r.kind == "swipe_hint"]

    lines: list[str] = []
    candidates: list[tuple[float, int, int, str]] = []

    if not pulsing and moving:
        lines.append(
            "motion_noise: only moving_sprites detected (likely idle animation); "
            "deprioritize motion regions for tap."
        )

    if swipe_hints:
        sh = swipe_hints[0]
        direction = str(sh.extra.get("direction", motion.swipe_direction or ""))
        if direction:
            lines.append(f"swipe_hint: direction={direction}")

    if pulsing:
        lines.append("tutorial_candidates:")
        ranked: list[tuple[float, MotionRegion, OcrBbox | None, float]] = []
        for pulse in pulsing:
            nearest: OcrBbox | None = None
            nearest_dist = float("inf")
            for bbox in bboxes:
                text = (bbox.text or "").strip()
                if not text:
                    continue
                d = _dist(pulse.cx, pulse.cy, bbox.cx, bbox.cy)
                if d < nearest_dist:
                    nearest_dist = d
                    nearest = bbox
            ocr_boost = 1.0
            if nearest is not None and nearest_dist <= radius:
                ocr_boost = 1.5
                if _BUTTON_RE.match((nearest.text or "").strip()):
                    ocr_boost = 2.0
            rank_score = pulse.score * ocr_boost * (1.0 + max(0.0, 1.0 - nearest_dist / radius))
            ranked.append((rank_score, pulse, nearest, nearest_dist))

        ranked.sort(key=lambda x: x[0], reverse=True)
        for rank, (score, pulse, nearest, dist) in enumerate(
            [(r[0], r[1], r[2], r[3]) for r in ranked], start=1,
        ):
            if nearest is not None and dist <= radius:
                text = (nearest.text or "").strip()[:40]
                line = (
                    f"  rank={rank}: pulse@({pulse.cx},{pulse.cy}) "
                    f"near OCR '{text}' dist={dist:.0f}px score={score:.2f}"
                )
                conf = "high" if _BUTTON_RE.match(text) else "medium"
                line += f" confidence={conf}"
            else:
                line = (
                    f"  rank={rank}: pulse@({pulse.cx},{pulse.cy}) "
                    f"no nearby OCR score={score:.2f}"
                )
                conf = "low"
            lines.append(line)
            reason = f"pulse_rank_{rank}"
            if nearest is not None and dist <= radius:
                reason = f"pulse_near_ocr:{(nearest.text or '')[:20]}"
            candidates.append((score, pulse.cx, pulse.cy, reason))

    if not lines:
        lines.append("spatial_fuse: no pulsing_fixed regions to fuse")

    top_tap: tuple[int, int] | None = None
    top_reason = "none"
    top_score = 0.0
    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)
        top_score, tx, ty, top_reason = candidates[0]
        top_tap = (tx, ty)

    return SpatialFuseResult(
        hints_text="\n".join(lines),
        top_tap_candidate=top_tap,
        top_tap_reason=top_reason,
        top_tap_score=top_score,
    )
