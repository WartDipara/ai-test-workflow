"""教程意图：检测需视觉定位（非 OCR 文字目标）的引导步。"""

from __future__ import annotations

import re

from game_agent.i18n import Concept, compile_lexicon_pattern, text_contains
from game_agent.models.tutorial_pulse import TutorialIntent
from game_agent.utils.ocr_util import OcrBbox

_TUTORIAL_VISUAL_RE = compile_lexicon_pattern(
    Concept.TUTORIAL_TAP_CARD,
    Concept.TUTORIAL_DEPLOY,
)
_PULSE_GUIDANCE_RE = compile_lexicon_pattern(
    Concept.TUTORIAL_TAP_CARD,
    Concept.TUTORIAL_DEPLOY,
    Concept.TUTORIAL_TAP_GLOW,
)
_CTA_EXCLUDE_RE = compile_lexicon_pattern(
    Concept.SPATIAL_BUTTON,
    Concept.SKIP,
    Concept.CONTINUE,
    Concept.CONFIRM,
)
_BATTLE_CTA_RE = re.compile(r"^战斗$|^battle$", re.IGNORECASE)


def _intent_kind_from_text(text: str) -> str:
    blob = text or ""
    if text_contains(blob, Concept.TUTORIAL_TAP_CARD):
        return "tap_card"
    if text_contains(blob, Concept.TUTORIAL_DEPLOY):
        return "deploy"
    if text_contains(blob, Concept.TUTORIAL_TAP_GLOW):
        return "tap_glow"
    return "tap_glow"


def _matching_trigger_phrase(ocr_summary: str) -> str:
    merged = ocr_summary or ""
    for concept in (
        Concept.TUTORIAL_TAP_CARD,
        Concept.TUTORIAL_DEPLOY,
        Concept.TUTORIAL_TAP_GLOW,
    ):
        from game_agent.i18n.match import all_phrases

        for phrase in all_phrases(concept):
            if phrase and phrase.lower() in merged.lower():
                return phrase
    return ""


def _is_excluded_cta_bbox(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _BATTLE_CTA_RE.match(t):
        return True
    if _CTA_EXCLUDE_RE.search(t) and not _TUTORIAL_VISUAL_RE.search(t):
        return True
    return False


def has_tutorial_visual_phrase(ocr_summary: str) -> bool:
    return bool(_TUTORIAL_VISUAL_RE.search(ocr_summary or ""))


def has_pulse_guidance_phrase(ocr_summary: str) -> bool:
    """战斗必杀/发光指引等需 OpenCV 脉冲连拍的 OCR 信号（不要求无 OCR 目标）。"""
    return bool(_PULSE_GUIDANCE_RE.search(ocr_summary or ""))


def detect_tutorial_visual_intent(
    ocr_summary: str,
    bboxes: list[OcrBbox] | None = None,
) -> TutorialIntent | None:
    """
    命中「点击卡牌/上阵/点我放必杀」等教程文案，且屏幕上无可直接点击的目标 OCR 行时返回意图。
    """
    merged = ocr_summary or ""
    if not has_pulse_guidance_phrase(merged):
        return None

    trigger = _matching_trigger_phrase(merged)
    kind = _intent_kind_from_text(merged)

    if bboxes and kind in ("tap_card", "deploy"):
        for bbox in bboxes:
            text = (bbox.text or "").strip()
            if not text or _is_excluded_cta_bbox(text):
                continue
            if _TUTORIAL_VISUAL_RE.search(text):
                continue
            if kind == "tap_card" and re.search(r"卡牌|卡片|card", text, re.IGNORECASE):
                return None

    return TutorialIntent(
        kind=kind,
        trigger_phrase=trigger,
        reason=f"tutorial_visual:{kind}",
    )


def needs_visual_tap_locator(
    ocr_summary: str,
    bboxes: list[OcrBbox] | None = None,
) -> bool:
    if has_pulse_guidance_phrase(ocr_summary) and not has_tutorial_visual_phrase(ocr_summary):
        return True
    return detect_tutorial_visual_intent(ocr_summary, bboxes) is not None


def find_tutorial_anchor_bbox(
    bboxes: list[OcrBbox],
    *,
    ocr_summary: str = "",
) -> OcrBbox | None:
    """找含教程触发词的 OCR 行（通常为对话气泡），作几何锚点。"""
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text:
            continue
        if _PULSE_GUIDANCE_RE.search(text):
            candidates.append((len(text), bbox))
            continue
        if ocr_summary and _matching_trigger_phrase(ocr_summary):
            phrase = _matching_trigger_phrase(ocr_summary)
            if phrase and phrase in text:
                candidates.append((len(text), bbox))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]
