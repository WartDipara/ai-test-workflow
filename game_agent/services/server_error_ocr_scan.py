"""OCR 区服错误 toast / 文案扫描（不依赖 keyboard 分区）。"""

from __future__ import annotations

import re

from game_agent.i18n import Concept, compile_lexicon_pattern, text_contains
from game_agent.models.server_connectivity_probe import ServerConnectivityProbe
from game_agent.utils.ocr_util import OcrBbox

_SERVER_ERROR_RE = compile_lexicon_pattern(
    Concept.SERVER_NOT_EXIST,
    Concept.CONNECTION_FAILED,
)

_DASH_ONLY_SLOT_RE = re.compile(r"^-{2,}$")


def find_server_error_text(bboxes: list[OcrBbox]) -> str | None:
    """返回首个匹配的区服错误 OCR 原文；无命中则 None。"""
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text:
            continue
        if _SERVER_ERROR_RE.search(text) or text_contains(
            text,
            Concept.SERVER_NOT_EXIST,
            Concept.CONNECTION_FAILED,
        ):
            return text[:200]
    return None


def probe_from_server_error_ocr(
    bboxes: list[OcrBbox],
    *,
    matched_text: str | None = None,
) -> ServerConnectivityProbe | None:
    """OCR 命中区服错误时合成 fail_fast 探针。"""
    hit = matched_text or find_server_error_text(bboxes)
    if not hit:
        return None
    return ServerConnectivityProbe(
        on_enter_game_screen=True,
        enter_button_visible=True,
        server_slot_status="error",
        server_list_likely_available=False,
        has_network_error_ui=True,
        confidence=0.95,
        reason=f"OCR detected server error UI: {hit}",
        recommendation="fail_fast",
    )


def band_has_dash_only_slot(
    bboxes: list[OcrBbox],
    *,
    band_y1: int,
    band_y2: int,
    band_x1: int,
    band_x2: int,
) -> bool:
    """区服带内是否仅有 dash 占位（----），无有效区服名。"""
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text:
            continue
        if not (band_x1 <= bbox.cx <= band_x2 and band_y1 <= bbox.cy <= band_y2):
            continue
        if _DASH_ONLY_SLOT_RE.match(text):
            return True
    return False
