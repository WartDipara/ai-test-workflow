"""OpenCV 局内动效分析：帧差、时序方差、光流、闪烁/运动分离。"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from game_agent.models.motion_probe import MotionProbeSection, MotionProbeResult, MotionRegion

logger = logging.getLogger(__name__)


def _colorize(gray: np.ndarray) -> np.ndarray:
    norm = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    return cv2.applyColorMap(norm.astype(np.uint8), cv2.COLORMAP_JET)


def _top_regions(
    heat: np.ndarray,
    *,
    min_area: int,
    top_k: int = 8,
) -> list[tuple[int, int, int, int, int, float]]:
    """返回 (cx, cy, x, y, w, h, mean_score) 列表。"""
    blurred = cv2.GaussianBlur(heat, (5, 5), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel), cv2.MORPH_CLOSE, kernel)
    n, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    regions: list[tuple[int, int, int, int, int, float]] = []
    for idx in range(1, n):
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        x = int(stats[idx, cv2.CC_STAT_LEFT])
        y = int(stats[idx, cv2.CC_STAT_TOP])
        w = int(stats[idx, cv2.CC_STAT_WIDTH])
        h = int(stats[idx, cv2.CC_STAT_HEIGHT])
        cx, cy = int(centroids[idx][0]), int(centroids[idx][1])
        roi = heat[y : y + h, x : x + w]
        score = float(np.mean(roi)) if roi.size else 0.0
        regions.append((cx, cy, x, y, w, h, score))
    regions.sort(key=lambda r: r[6] * (r[4] * r[5]), reverse=True)
    return regions[:top_k]


def _hsv_yellow_mask(bgr: np.ndarray) -> np.ndarray:
    """黄色高亮指引光圈 mask（0~255）。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower = np.array([15, 80, 120], dtype=np.uint8)
    upper = np.array([45, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    return mask


def _hsv_white_glow_mask(bgr: np.ndarray) -> np.ndarray:
    """白色/低饱和高亮教程光圈 mask（0~255）。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower = np.array([0, 0, 200], dtype=np.uint8)
    upper = np.array([180, 60, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    return mask


def _vertical_band(cy: int, screen_h: int) -> str:
    if screen_h <= 0:
        return "middle"
    ratio = cy / float(screen_h)
    if ratio < 0.34:
        return "top"
    if ratio < 0.67:
        return "middle"
    return "lower"


def _dominant_flow_direction(flow: np.ndarray) -> tuple[str, float]:
    mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
    threshold = max(float(np.percentile(mag, 85)), 0.5)
    mask = mag >= threshold
    if not np.any(mask):
        return "", 0.0
    angles = ang[mask]
    mags = mag[mask]
    # 加权平均角度
    sin_sum = float(np.sum(np.sin(angles) * mags))
    cos_sum = float(np.sum(np.cos(angles) * mags))
    if abs(sin_sum) < 1e-6 and abs(cos_sum) < 1e-6:
        return "", 0.0
    mean_ang = float(np.arctan2(sin_sum, cos_sum))
    deg = (mean_ang * 180.0 / np.pi) % 360.0
    magnitude = float(np.mean(mags))
    if 45 <= deg < 135:
        direction = "down"
    elif 135 <= deg < 225:
        direction = "left"
    elif 225 <= deg < 315:
        direction = "up"
    else:
        direction = "right"
    return direction, magnitude


def _build_summary(
    regions: list[MotionRegion],
    *,
    pairwise_mean_diff: float,
    swipe_direction: str,
    swipe_magnitude: float,
    frame_count: int = 0,
    interval_s: float = 0.0,
) -> str:
    window_s = max(0.0, (frame_count - 1) * interval_s) if frame_count > 1 else 0.0
    lines = [
        f"capture_window: {frame_count} frames @ {interval_s:.1f}s interval (~{window_s:.1f}s total)",
        f"pairwise_mean_absdiff={pairwise_mean_diff:.2f}",
    ]
    if swipe_direction:
        lines.append(f"swipe_hint: direction={swipe_direction} magnitude={swipe_magnitude:.2f}")
    pulsing = [r for r in regions if r.kind == "pulsing_fixed"]
    moving = [r for r in regions if r.kind == "moving_sprite"]
    if pulsing:
        lines.append("pulsing_fixed:")
        for i, r in enumerate(pulsing, 1):
            lines.append(
                f"  P{i}: center=({r.cx},{r.cy}) area={r.area} score={r.score:.2f}"
            )
    else:
        lines.append("pulsing_fixed: none")
    if moving:
        lines.append("moving_sprites (deprioritize):")
        for i, r in enumerate(moving, 1):
            lines.append(
                f"  M{i}: center=({r.cx},{r.cy}) area={r.area} score={r.score:.2f}"
            )
    else:
        lines.append("moving_sprites: none")
    return "\n".join(lines)


def run_motion_probe(
    frame_paths: list[Path],
    *,
    artifact_root: Path | None = None,
    round_id: int = 0,
    motion_cfg: MotionProbeSection | None = None,
) -> MotionProbeResult:
    """分析连拍帧序列，输出动效区域与文本摘要。"""
    cfg = motion_cfg or MotionProbeSection()
    if len(frame_paths) < 2:
        return MotionProbeResult(
            regions=[],
            summary_text="motion_probe: insufficient frames",
            pairwise_mean_diff=0.0,
        )

    frames_bgr = [cv2.imread(str(p), cv2.IMREAD_COLOR) for p in frame_paths]
    if any(f is None for f in frames_bgr):
        return MotionProbeResult(
            regions=[],
            summary_text="motion_probe: failed to read frames",
            pairwise_mean_diff=0.0,
        )

    h, w = frames_bgr[0].shape[:2]
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames_bgr]
    stack = np.stack(grays, axis=0).astype(np.float32)

    pair_means: list[float] = []
    for i in range(1, len(grays)):
        pair_means.append(float(np.mean(cv2.absdiff(grays[i], grays[i - 1]))))
    pairwise_mean = float(np.mean(pair_means)) if pair_means else 0.0

    max_diff = np.zeros((h, w), dtype=np.float32)
    for i in range(len(grays)):
        for j in range(i + 1, len(grays)):
            max_diff = np.maximum(max_diff, cv2.absdiff(grays[i], grays[j]).astype(np.float32))

    temporal_std = np.std(stack, axis=0)
    flow_sum = np.zeros((h, w), dtype=np.float32)
    flow_count = 0
    swipe_direction = ""
    swipe_magnitude = 0.0
    for i in range(1, len(grays)):
        flow = cv2.calcOpticalFlowFarneback(
            grays[i - 1],
            grays[i],
            None,
            0.5,
            3,
            15,
            3,
            5,
            1.2,
            0,
        )
        mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
        flow_sum += mag
        flow_count += 1
        direction, magnitude = _dominant_flow_direction(flow)
        if magnitude > swipe_magnitude:
            swipe_direction = direction
            swipe_magnitude = magnitude
    flow_avg = flow_sum / max(flow_count, 1)

    std_n = temporal_std / (float(temporal_std.max()) + 1e-6)
    flow_n = flow_avg / (float(flow_avg.max()) + 1e-6)
    pulse_score = std_n * (1.0 - np.clip(flow_n * 2.0, 0, 1))
    motion_score = flow_n

    if cfg.hsv_yellow_boost:
        yellow_masks = [_hsv_yellow_mask(f) for f in frames_bgr]
        yellow_stack = np.stack(yellow_masks, axis=0).astype(np.float32) / 255.0
        yellow_std = np.std(yellow_stack, axis=0)
        yellow_n = yellow_std / (float(yellow_std.max()) + 1e-6)
        pulse_score = np.maximum(pulse_score, yellow_n * (1.0 - np.clip(flow_n * 1.5, 0, 1)))

    if cfg.hsv_white_glow_boost:
        white_masks = [_hsv_white_glow_mask(f) for f in frames_bgr]
        white_stack = np.stack(white_masks, axis=0).astype(np.float32) / 255.0
        white_std = np.std(white_stack, axis=0)
        white_n = white_std / (float(white_std.max()) + 1e-6)
        pulse_score = np.maximum(pulse_score, white_n * (1.0 - np.clip(flow_n * 1.5, 0, 1)))

    pulse_u8 = cv2.normalize(pulse_score, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    motion_u8 = cv2.normalize(motion_score, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

    regions: list[MotionRegion] = []
    max_score = 255.0
    for cx, cy, x, y, bw, bh, score in _top_regions(
        pulse_u8, min_area=cfg.pulse_min_area,
    ):
        regions.append(
            MotionRegion(
                kind="pulsing_fixed",
                cx=cx,
                cy=cy,
                bbox=(x, y, bw, bh),
                area=bw * bh,
                score=round(score / max_score, 3),
                extra={"band": _vertical_band(cy, h)},
            )
        )
    for cx, cy, x, y, bw, bh, score in _top_regions(
        motion_u8, min_area=cfg.motion_min_area,
    ):
        regions.append(
            MotionRegion(
                kind="moving_sprite",
                cx=cx,
                cy=cy,
                bbox=(x, y, bw, bh),
                area=bw * bh,
                score=round(score / max_score, 3),
            )
        )
    if swipe_direction and swipe_magnitude > 0.5:
        regions.append(
            MotionRegion(
                kind="swipe_hint",
                cx=w // 2,
                cy=h // 2,
                bbox=(0, 0, w, h),
                area=w * h,
                score=min(1.0, swipe_magnitude / 5.0),
                extra={"direction": swipe_direction, "magnitude": round(swipe_magnitude, 2)},
            )
        )

    heatmap_path: Path | None = None
    pulse_overlay_path: Path | None = None
    annotated_path: Path | None = None
    if artifact_root is not None and cfg.save_heatmaps:
        artifact_root.mkdir(parents=True, exist_ok=True)
        prefix = f"motion_{round_id:03d}"
        heatmap_path = artifact_root / f"{prefix}_pulse_heat.png"
        cv2.imwrite(str(heatmap_path), _colorize(pulse_u8))
        pulse_overlay = cv2.addWeighted(
            frames_bgr[0], 0.55, _colorize(pulse_u8), 0.45, 0,
        )
        pulse_overlay_path = artifact_root / f"{prefix}_pulse_overlay.png"
        cv2.imwrite(str(pulse_overlay_path), pulse_overlay)
        annotated = frames_bgr[0].copy()
        for i, r in enumerate([x for x in regions if x.kind == "pulsing_fixed"], 1):
            x, y, bw, bh = r.bbox
            cv2.rectangle(annotated, (x, y), (x + bw, y + bh), (0, 255, 0), 2)
            cv2.circle(annotated, (r.cx, r.cy), 6, (0, 0, 255), -1)
            cv2.putText(
                annotated, f"P{i}", (x, max(20, y - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA,
            )
        for i, r in enumerate([x for x in regions if x.kind == "moving_sprite"], 1):
            x, y, bw, bh = r.bbox
            cv2.rectangle(annotated, (x, y), (x + bw, y + bh), (0, 165, 255), 2)
            cv2.putText(
                annotated, f"M{i}", (x, y + bh + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1, cv2.LINE_AA,
            )
        annotated_path = artifact_root / f"{prefix}_annotated.png"
        cv2.imwrite(str(annotated_path), annotated)

    summary = _build_summary(
        regions,
        pairwise_mean_diff=pairwise_mean,
        swipe_direction=swipe_direction,
        swipe_magnitude=swipe_magnitude,
        frame_count=len(frame_paths),
        interval_s=float(cfg.interval_s),
    )
    logger.info(
        "[MotionProbe] round=%d frames=%d pulse=%d motion=%d diff=%.2f",
        round_id,
        len(frame_paths),
        sum(1 for r in regions if r.kind == "pulsing_fixed"),
        sum(1 for r in regions if r.kind == "moving_sprite"),
        pairwise_mean,
    )
    return MotionProbeResult(
        regions=regions,
        summary_text=summary,
        pairwise_mean_diff=pairwise_mean,
        heatmap_path=heatmap_path,
        pulse_overlay_path=pulse_overlay_path,
        annotated_path=annotated_path,
        swipe_direction=swipe_direction,
        swipe_magnitude=swipe_magnitude,
    )
