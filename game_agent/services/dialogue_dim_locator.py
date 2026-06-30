"""对话压暗背景区域定位：OpenCV 亮度分割 + OCR/亮框排除。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from game_agent.models.dialogue_dim_tap import DialogueDimTapSection
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DimTapRegion:
    x1: int
    y1: int
    x2: int
    y2: int
    cx: int
    cy: int
    area_ratio: float
    reason: str


@dataclass(frozen=True, slots=True)
class DialogueDimLocateResult:
    regions: list[DimTapRegion]
    recommended: DimTapRegion | None
    dark_threshold: int
    message: str
    annotated_path: Path | None = None


def _default_cfg() -> DialogueDimTapSection:
    return DialogueDimTapSection()


def _expand_bbox(bbox: OcrBbox, margin: int, w: int, h: int) -> tuple[int, int, int, int]:
    x1 = max(0, int(bbox.x1) - margin)
    y1 = max(0, int(bbox.y1) - margin)
    x2 = min(w, int(bbox.x2) + margin)
    y2 = min(h, int(bbox.y2) + margin)
    if x2 <= x1:
        x2 = min(w, x1 + 1)
    if y2 <= y1:
        y2 = min(h, y1 + 1)
    return x1, y1, x2, y2


def _largest_bright_blob_mask(
    gray: np.ndarray,
    *,
    y_max: int,
    bright_percentile: float = 75.0,
) -> np.ndarray:
    h, w = gray.shape[:2]
    upper = gray[: max(1, y_max), :]
    if upper.size == 0:
        return np.zeros((h, w), dtype=np.uint8)
    thresh = float(np.percentile(gray, bright_percentile))
    bright = (upper > thresh).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel)
    n, _, stats, _ = cv2.connectedComponentsWithStats(bright, connectivity=8)
    mask = np.zeros((h, w), dtype=np.uint8)
    if n <= 1:
        return mask
    best_idx = 1
    best_area = 0
    for idx in range(1, n):
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area > best_area:
            best_area = area
            best_idx = idx
    x = int(stats[best_idx, cv2.CC_STAT_LEFT])
    y = int(stats[best_idx, cv2.CC_STAT_TOP])
    bw = int(stats[best_idx, cv2.CC_STAT_WIDTH])
    bh = int(stats[best_idx, cv2.CC_STAT_HEIGHT])
    mask[y : y + bh, x : x + bw] = 255
    return mask


def _rank_region(
    region: DimTapRegion,
    *,
    screen_w: int,
    screen_h: int,
    cfg: DialogueDimTapSection,
) -> float:
    score = region.area_ratio * 2.0
    cx_target = screen_w * 0.5
    score -= abs(region.cx - cx_target) / max(screen_w, 1) * 0.8
    y_mid = (cfg.prefer_tap_y_min_ratio + cfg.prefer_tap_y_max_ratio) * 0.5 * screen_h
    if cfg.prefer_tap_y_min_ratio * screen_h <= region.cy <= cfg.prefer_tap_y_max_ratio * screen_h:
        score += 1.5
    score -= abs(region.cy - y_mid) / max(screen_h, 1) * 0.5
    return score


def locate_dialogue_dim_regions(
    image_path: Path | str,
    *,
    bboxes: list[OcrBbox] | None = None,
    screen_w: int = 0,
    screen_h: int = 0,
    cfg: DialogueDimTapSection | None = None,
    artifact_root: Path | None = None,
    annotate_name: str = "dialogue_dim_annotated.png",
) -> DialogueDimLocateResult:
    """在压暗对话画面上定位可点击的暗色背景区域。"""
    cfg = cfg or _default_cfg()
    path = Path(image_path)
    bboxes = bboxes or []

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return DialogueDimLocateResult(
            regions=[],
            recommended=None,
            dark_threshold=0,
            message=f"failed to read image: {path}",
        )

    h, w = img.shape[:2]
    if screen_w <= 0:
        screen_w = w
    if screen_h <= 0:
        screen_h = h

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dark_threshold = int(np.percentile(gray, cfg.dark_percentile))
    dark_mask = (gray < dark_threshold).astype(np.uint8) * 255

    exclude = np.zeros((h, w), dtype=np.uint8)
    top_h = int(h * cfg.top_exclude_ratio)
    bot_y = int(h * (1.0 - cfg.bottom_exclude_ratio))
    if top_h > 0:
        exclude[:top_h, :] = 255
    if bot_y < h:
        exclude[bot_y:, :] = 255

    margin = int(cfg.ocr_exclude_margin_px)
    for bbox in bboxes:
        x1, y1, x2, y2 = _expand_bbox(bbox, margin, w, h)
        exclude[y1:y2, x1:x2] = 255

    bright_y_max = int(h * cfg.bright_dialogue_y_ratio)
    bright_blob = _largest_bright_blob_mask(gray, y_max=bright_y_max)
    exclude = cv2.bitwise_or(exclude, bright_blob)

    candidate = cv2.bitwise_and(dark_mask, cv2.bitwise_not(exclude))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN, kernel)
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, kernel)

    min_area = int(w * h * cfg.min_region_area_ratio)
    n, _, stats, centroids = cv2.connectedComponentsWithStats(candidate, connectivity=8)
    regions: list[DimTapRegion] = []
    for idx in range(1, n):
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        x = int(stats[idx, cv2.CC_STAT_LEFT])
        y = int(stats[idx, cv2.CC_STAT_TOP])
        bw = int(stats[idx, cv2.CC_STAT_WIDTH])
        bh = int(stats[idx, cv2.CC_STAT_HEIGHT])
        cx = int(centroids[idx][0])
        cy = int(centroids[idx][1])
        area_ratio = area / max(w * h, 1)
        reason = f"dim_blob area={area_ratio:.2f} center=({cx},{cy})"
        regions.append(
            DimTapRegion(
                x1=x,
                y1=y,
                x2=x + bw,
                y2=y + bh,
                cx=cx,
                cy=cy,
                area_ratio=area_ratio,
                reason=reason,
            ),
        )

    if not regions:
        return DialogueDimLocateResult(
            regions=[],
            recommended=None,
            dark_threshold=dark_threshold,
            message="no dim region above min area",
        )

    regions.sort(
        key=lambda r: _rank_region(r, screen_w=screen_w, screen_h=screen_h, cfg=cfg),
        reverse=True,
    )
    recommended = regions[0]

    annotated_path: Path | None = None
    if cfg.save_annotate and artifact_root is not None:
        artifact_root.mkdir(parents=True, exist_ok=True)
        annotated = img.copy()
        for i, reg in enumerate(regions[:5], start=1):
            color = (0, 255, 0) if i == 1 else (0, 180, 0)
            cv2.rectangle(annotated, (reg.x1, reg.y1), (reg.x2, reg.y2), color, 2)
            cv2.putText(
                annotated,
                f"D{i}",
                (reg.x1, max(20, reg.y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )
        cv2.circle(annotated, (recommended.cx, recommended.cy), 8, (0, 0, 255), -1)
        cv2.putText(
            annotated,
            "tap",
            (recommended.cx + 10, recommended.cy),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
        annotated_path = artifact_root / annotate_name
        cv2.imwrite(str(annotated_path), annotated)

    logger.info(
        "[DialogueDim] regions=%d recommended=(%d,%d) thresh=%d | %s",
        len(regions),
        recommended.cx,
        recommended.cy,
        dark_threshold,
        recommended.reason[:80],
    )
    return DialogueDimLocateResult(
        regions=regions,
        recommended=recommended,
        dark_threshold=dark_threshold,
        message=recommended.reason,
        annotated_path=annotated_path,
    )


def locate_dialogue_dim_tap(
    image_path: Path | str,
    *,
    bboxes: list[OcrBbox] | None = None,
    screen_w: int = 0,
    screen_h: int = 0,
    cfg: DialogueDimTapSection | None = None,
    artifact_root: Path | None = None,
    annotate_name: str = "dialogue_dim_annotated.png",
) -> tuple[int, int] | None:
    """返回推荐暗色点击坐标；无候选时 None。"""
    result = locate_dialogue_dim_regions(
        image_path,
        bboxes=bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
        cfg=cfg,
        artifact_root=artifact_root,
        annotate_name=annotate_name,
    )
    if result.recommended is None:
        return None
    return result.recommended.cx, result.recommended.cy
