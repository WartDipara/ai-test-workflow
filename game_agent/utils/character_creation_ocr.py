from __future__ import annotations

from game_agent.i18n import CHARACTER_CREATION_OCR_MARKERS, Concept, match_phrases_in_text


def match_character_creation_ocr(ocr_summary: str) -> list[str]:
    """返回 OCR 文本中命中的创角相关关键词（去重、保持顺序）。"""
    return match_phrases_in_text(
        ocr_summary or "",
        Concept.CHARACTER_CREATION,
        ascii_word_boundary=False,
    )
