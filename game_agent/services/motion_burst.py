"""ADB 连拍采集，供局内 motion_probe 使用。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from game_agent.models.motion_probe import MotionProbeSection
from game_agent.services.adb_service import AdbService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MotionBurstResult:
    keyframe_path: Path
    frame_paths: list[Path]
    interval_s: float


def capture_motion_burst(
    adb: AdbService,
    artifact_root: Path,
    *,
    prefix: str,
    motion_cfg: MotionProbeSection | None = None,
) -> MotionBurstResult:
    """连续截帧；keyframe 为最后一帧（最接近决策时刻）。"""
    cfg = motion_cfg or MotionProbeSection()
    frame_count = max(2, int(cfg.frame_count))
    interval_s = float(cfg.interval_s)
    ts = datetime.now().strftime("%H%M%S_%f")
    paths: list[Path] = []

    for i in range(1, frame_count + 1):
        shot = artifact_root / f"{prefix}_burst_{ts}_{i:02d}.png"
        adb.screencap_png(shot)
        paths.append(shot.resolve())
        if i < frame_count and interval_s > 0:
            adb.wait_seconds(interval_s)

    keyframe = paths[-1]
    logger.info(
        "[MotionBurst] captured %d frames interval=%.2fs window~=%.1fs keyframe=%s",
        len(paths),
        interval_s,
        (len(paths) - 1) * interval_s,
        keyframe.name,
    )
    return MotionBurstResult(
        keyframe_path=keyframe,
        frame_paths=paths,
        interval_s=interval_s,
    )
