"""快循环：跟踪闪烁热点并在峰值时刻点击（Phase 3）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from game_agent.models.motion_probe import MotionProbeSection
from game_agent.services.adb_service import AdbService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FastLoopTapResult:
    tapped: bool
    x: int
    y: int
    peak_score: float
    frames_captured: int
    message: str


def track_pulse_and_tap(
    adb: AdbService,
    artifact_root: Path,
    *,
    target_cx: int,
    target_cy: int,
    radius_px: int = 80,
    screen_w: int,
    screen_h: int,
    motion_cfg: MotionProbeSection | None = None,
    prefix: str = "fast_loop",
) -> FastLoopTapResult:
    """
    在目标坐标附近循环截帧，帧差峰值时点击。
    用于 behavior step intent 含 tap_glowing_hint 的场景。
    """
    cfg = motion_cfg or MotionProbeSection()
    max_frames = max(5, int(cfg.fast_loop_max_frames))
    interval_s = float(cfg.fast_loop_interval_s)

    x1 = max(0, target_cx - radius_px)
    y1 = max(0, target_cy - radius_px)
    x2 = min(screen_w, target_cx + radius_px)
    y2 = min(screen_h, target_cy + radius_px)
    if x2 <= x1 or y2 <= y1:
        return FastLoopTapResult(
            tapped=False, x=target_cx, y=target_cy, peak_score=0.0,
            frames_captured=0, message="invalid_roi",
        )

    prev_gray: np.ndarray | None = None
    peak_score = -1.0
    peak_frame_idx = 0
    ts = datetime.now().strftime("%H%M%S_%f")

    for i in range(1, max_frames + 1):
        shot = artifact_root / f"{prefix}_{ts}_{i:02d}.png"
        adb.screencap_png(shot)
        bgr = cv2.imread(str(shot), cv2.IMREAD_COLOR)
        if bgr is None:
            continue
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        roi = gray[y1:y2, x1:x2]
        if prev_gray is not None:
            prev_roi = prev_gray[y1:y2, x1:x2]
            if prev_roi.shape == roi.shape:
                diff = cv2.absdiff(roi, prev_roi)
                score = float(np.mean(diff))
                if score > peak_score:
                    peak_score = score
                    peak_frame_idx = i
        prev_gray = gray
        if i < max_frames and interval_s > 0:
            adb.wait_seconds(interval_s)

    if peak_score < 0:
        adb.tap(target_cx, target_cy, width=screen_w, height=screen_h)
        return FastLoopTapResult(
            tapped=True, x=target_cx, y=target_cy, peak_score=0.0,
            frames_captured=max_frames,
            message="fallback_direct_tap_no_diff",
        )

    adb.tap(target_cx, target_cy, width=screen_w, height=screen_h)
    logger.info(
        "[MotionFastLoop] tap (%d,%d) peak_score=%.2f frame=%d",
        target_cx, target_cy, peak_score, peak_frame_idx,
    )
    return FastLoopTapResult(
        tapped=True,
        x=target_cx,
        y=target_cy,
        peak_score=peak_score,
        frames_captured=max_frames,
        message=f"peak_tap frame={peak_frame_idx}",
    )
