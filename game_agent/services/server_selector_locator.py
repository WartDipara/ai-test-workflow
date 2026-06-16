"""进入游戏按钮锚点 + 上方区服 OCR 语义定位（协议行/Enter 结构分区）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from game_agent.services.checkbox_locator import find_privacy_terms_anchor
from game_agent.utils.ocr_util import OcrBbox

_ENTER_GAME_RE = re.compile(
    r"踏入仙途|开始游戏|进入游戏|开始冒险|Enter\s*Game|Start\s*Game|^Start$|"
    r"^Enter$|Play\s*Now|进入",
    re.IGNORECASE,
)

_EXCLUDE_TARGET_RE = re.compile(
    r"sub-?account|login|password|账号|密码|登录|协议|隐私|privacy|agree|"
    r"copyright|publisher|版本|support|forgot|health\s*advisory|cadpa|适龄",
    re.IGNORECASE,
)

_SERVER_HINT_RE = re.compile(
    r"选服|区服|服务器|server|click\s*to\s*select|select\s*server|线路|realm|zone|"
    r"role\s*name|exclusive|sponsored|删档|内测|\d+服|-{2,}",
    re.IGNORECASE,
)

_PROBE_NAME_CHUNK_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{3,}")

_NOISE_TARGET_CHARS = frozenset("+-*,.·□☐✓√…:;!?")

BAND_HEIGHT_PX = 220
MIN_GAP_ABOVE_ENTER_PX = 12
FALLBACK_OFFSET_PX = 100
ENTER_POSITION_TOLERANCE_PX = 48
SERVER_ZONE_TOP_RATIO = 0.35


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
    source: Literal["ocr", "fallback", "unresolved"]


def find_enter_game_bbox(bboxes: list[OcrBbox]) -> OcrBbox | None:
    """匹配进入游戏主 CTA（排除 Login 等）。"""
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text or _EXCLUDE_TARGET_RE.search(text):
            continue
        if _ENTER_GAME_RE.search(text):
            candidates.append((bbox.cy, bbox))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def server_band(enter: OcrBbox, screen_w: int, screen_h: int) -> ServerBand:
    """进入按钮上方的区服带矩形（水平范围；空槽检测等仍用此 x 带）。"""
    half_w = max(enter.x2 - enter.x1, 80) // 2
    margin = int(half_w * 0.25)
    x1 = max(0, enter.cx - half_w - margin)
    x2 = min(screen_w - 1, enter.cx + half_w + margin)
    y2 = max(0, enter.y1 - MIN_GAP_ABOVE_ENTER_PX)
    y1 = max(0, y2 - BAND_HEIGHT_PX)
    return ServerBand(x1=x1, y1=y1, x2=x2, y2=y2)


def _horizontal_distance(bbox: OcrBbox, enter: OcrBbox) -> int:
    return abs(bbox.cx - enter.cx)


def _is_noise_target(text: str) -> bool:
    t = text.strip()
    if not t or len(t) <= 1:
        return True
    if all(c in _NOISE_TARGET_CHARS or c.isspace() for c in t):
        return True
    return False


def _upper_limit_y(enter: OcrBbox, agree_anchor) -> int:
    if agree_anchor is not None:
        return agree_anchor.line_y1 - MIN_GAP_ABOVE_ENTER_PX
    return enter.y1 - MIN_GAP_ABOVE_ENTER_PX


def _top_limit_y(enter: OcrBbox, screen_h: int) -> int:
    return max(0, enter.y1 - int(screen_h * SERVER_ZONE_TOP_RATIO))


def _in_server_horizontal_band(bbox: OcrBbox, band: ServerBand) -> bool:
    return band.x1 <= bbox.cx <= band.x2


def _in_server_vertical_zone(
    bbox: OcrBbox,
    enter: OcrBbox,
    *,
    agree_anchor,
    screen_h: int,
) -> bool:
    if bbox.cy >= enter.y1:
        return False
    upper = _upper_limit_y(enter, agree_anchor)
    if bbox.y2 >= upper:
        return False
    top = _top_limit_y(enter, screen_h)
    if bbox.cy < top:
        return False
    return True


def _probe_name_boost(text: str, probe_server_name_hint: str) -> int:
    """probe reason 中的服名字串与 OCR 文本匹配时降低排序代价。"""
    hint = (probe_server_name_hint or "").strip()
    if not hint or not text:
        return 50
    merged = text.strip()
    for chunk in _PROBE_NAME_CHUNK_RE.findall(hint):
        if len(chunk) >= 3 and chunk.lower() in merged.lower():
            return 0
    return 50


def has_server_hint_above_enter(
    bboxes: list[OcrBbox],
    enter: OcrBbox,
    *,
    screen_w: int,
    screen_h: int,
) -> bool:
    """Enter 上方是否存在区服语义 OCR（用于禁止静默 fallback）。"""
    agree = find_privacy_terms_anchor(bboxes)
    band = server_band(enter, screen_w, screen_h)
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text or _is_noise_target(text):
            continue
        if _EXCLUDE_TARGET_RE.search(text):
            continue
        if not _in_server_vertical_zone(
            bbox, enter, agree_anchor=agree, screen_h=screen_h
        ):
            continue
        if not _in_server_horizontal_band(bbox, band):
            continue
        if _SERVER_HINT_RE.search(text):
            return True
    return False


def _server_slot_candidates(
    bboxes: list[OcrBbox],
    enter: OcrBbox,
    *,
    screen_w: int,
    screen_h: int,
    probe_server_name_hint: str = "",
) -> list[tuple[int, int, int, OcrBbox]]:
    agree = find_privacy_terms_anchor(bboxes)
    band = server_band(enter, screen_w, screen_h)
    ranked: list[tuple[int, int, int, OcrBbox]] = []
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text or _is_noise_target(text):
            continue
        if _EXCLUDE_TARGET_RE.search(text):
            continue
        if not _in_server_vertical_zone(
            bbox, enter, agree_anchor=agree, screen_h=screen_h
        ):
            continue
        if not _in_server_horizontal_band(bbox, band):
            continue
        vertical_gap = enter.y1 - bbox.y2
        hint_bonus = 0 if _SERVER_HINT_RE.search(text) else 40
        hint_bonus = min(hint_bonus, _probe_name_boost(text, probe_server_name_hint))
        length_bonus = 0 if len(text) >= 8 else 8
        ranked.append(
            (
                vertical_gap + hint_bonus + length_bonus,
                _horizontal_distance(bbox, enter),
                -len(text),
                bbox,
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return ranked


def locate_server_selector_target(
    bboxes: list[OcrBbox],
    *,
    screen_w: int,
    screen_h: int,
    probe_server_name_hint: str = "",
) -> tuple[ServerSelectorTarget | None, OcrBbox | None]:
    """
    定位区服入口坐标。返回 (target, enter_bbox)；enter 缺失时 target 也为 None。

    优先 OCR 语义候选（Enter/协议行垂直分区）；无候选时不静默几何 fallback。
    若 Enter 上方仍有区服语义 OCR 却未入选，返回 source=unresolved。
    """
    enter = find_enter_game_bbox(bboxes)
    if enter is None:
        return None, None

    candidates = _server_slot_candidates(
        bboxes,
        enter,
        screen_w=screen_w,
        screen_h=screen_h,
        probe_server_name_hint=probe_server_name_hint,
    )
    if candidates:
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

    if has_server_hint_above_enter(bboxes, enter, screen_w=screen_w, screen_h=screen_h):
        return (
            ServerSelectorTarget(cx=0, cy=0, label="", source="unresolved"),
            enter,
        )

    return None, enter


def fingerprint_server_band(
    bboxes: list[OcrBbox],
    enter: OcrBbox,
    screen_w: int,
    screen_h: int,
) -> set[str]:
    agree = find_privacy_terms_anchor(bboxes)
    band = server_band(enter, screen_w, screen_h)
    return {
        b.text.strip()
        for b in bboxes
        if b.text.strip()
        and _in_server_vertical_zone(
            b, enter, agree_anchor=agree, screen_h=screen_h
        )
        and _in_server_horizontal_band(b, band)
    }
