"""进入游戏按钮锚点 + 上方区服带 OCR/几何定位。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from game_agent.utils.ocr_util import OcrBbox

_ENTER_GAME_RE = re.compile(
    r"踏入仙途|开始游戏|进入游戏|开始冒险|Enter\s*Game|Start\s*Game|^Start$|"
    r"^Enter$|Play\s*Now|进入",
    re.IGNORECASE,
)

_EXCLUDE_TARGET_RE = re.compile(
    r"sub-?account|login|password|账号|密码|登录|协议|隐私|privacy|agree|"
    r"copyright|publisher|版本|support|forgot",
    re.IGNORECASE,
)

_SERVER_HINT_RE = re.compile(
    r"选服|区服|服务器|server|click\s*to\s*select|select\s*server|线路|realm|zone|"
    r"role\s*name|exclusive|sponsored|-{2,}",
    re.IGNORECASE,
)

_NOISE_TARGET_CHARS = frozenset("+-*,.·□☐✓√…:;!?")

BAND_HEIGHT_PX = 220
MIN_GAP_ABOVE_ENTER_PX = 12
FALLBACK_OFFSET_PX = 100
ENTER_POSITION_TOLERANCE_PX = 48


@dataclass(frozen=True, slots=True)
class ServerBand:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True, slots=True)
class ServerSelectorTarget:
    cx: int
    cy: int
    label: str
    source: Literal["ocr", "fallback"]


def find_enter_game_bbox(bboxes: list[OcrBbox]) -> OcrBbox | None:
    """匹配进入游戏主 CTA（排除 Login 等）。"""
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text or _EXCLUDE_TARGET_RE.search(text):
            continue
        if _ENTER_GAME_RE.search(text):
            # 优先 y 更大（更靠下的主按钮）
            candidates.append((bbox.cy, bbox))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def server_band(enter: OcrBbox, screen_w: int, screen_h: int) -> ServerBand:
    """进入按钮上方的区服带矩形。"""
    half_w = max(enter.x2 - enter.x1, 80) // 2
    margin = int(half_w * 0.25)
    x1 = max(0, enter.cx - half_w - margin)
    x2 = min(screen_w - 1, enter.cx + half_w + margin)
    y2 = max(0, enter.y1 - MIN_GAP_ABOVE_ENTER_PX)
    y1 = max(0, y2 - BAND_HEIGHT_PX)
    return ServerBand(x1=x1, y1=y1, x2=x2, y2=y2)


def _in_band(bbox: OcrBbox, band: ServerBand) -> bool:
    return (
        band.x1 <= bbox.cx <= band.x2
        and band.y1 <= bbox.cy <= band.y2
    )


def _horizontal_distance(bbox: OcrBbox, enter: OcrBbox) -> int:
    return abs(bbox.cx - enter.cx)


def _is_noise_target(text: str) -> bool:
    """排除单字符标点、checkbox 残片等 OCR 噪声。"""
    t = text.strip()
    if not t or len(t) <= 1:
        return True
    if all(c in _NOISE_TARGET_CHARS or c.isspace() for c in t):
        return True
    return False


def locate_server_selector_target(
    bboxes: list[OcrBbox],
    *,
    screen_w: int,
    screen_h: int,
) -> tuple[ServerSelectorTarget | None, OcrBbox | None]:
    """
    定位区服入口坐标。返回 (target, enter_bbox)；enter 缺失时 target 也为 None。
    """
    enter = find_enter_game_bbox(bboxes)
    if enter is None:
        return None, None

    band = server_band(enter, screen_w, screen_h)
    candidates: list[tuple[int, int, int, OcrBbox]] = []
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text or not _in_band(bbox, band):
            continue
        if _is_noise_target(text):
            continue
        if _EXCLUDE_TARGET_RE.search(text):
            continue
        if bbox.cy >= enter.y1:
            continue
        vertical_gap = enter.y1 - bbox.y2
        hint_bonus = 0 if _SERVER_HINT_RE.search(text) else 50
        length_bonus = 0 if len(text) >= 8 else 10
        candidates.append(
            (
                vertical_gap + hint_bonus + length_bonus,
                _horizontal_distance(bbox, enter),
                -len(text),
                bbox,
            )
        )

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        best = candidates[0][3]
        return (
            ServerSelectorTarget(
                cx=best.cx,
                cy=best.cy,
                label=best.text[:80],
                source="ocr",
            ),
            enter,
        )

    fb_y = max(0, enter.y1 - FALLBACK_OFFSET_PX)
    return (
        ServerSelectorTarget(
            cx=enter.cx,
            cy=fb_y,
            label="",
            source="fallback",
        ),
        enter,
    )


def fingerprint_server_band(
    bboxes: list[OcrBbox],
    enter: OcrBbox,
    screen_w: int,
    screen_h: int,
) -> set[str]:
    band = server_band(enter, screen_w, screen_h)
    return {
        b.text.strip()
        for b in bboxes
        if b.text.strip() and _in_band(b, band)
    }
