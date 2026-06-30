from __future__ import annotations

from game_agent.utils.character_creation_ocr import match_character_creation_ocr
from game_agent.i18n import IN_GAME_HUD_OCR_MARKERS, Concept, match_phrases_in_text


def match_in_game_hud_ocr(ocr_summary: str) -> list[str]:
    """Return matched in-game HUD keywords (deduped, stable order)."""
    return match_phrases_in_text(ocr_summary or "", Concept.IN_GAME_HUD)


def should_trigger_in_game_hud_check(ocr_summary: str) -> tuple[bool, list[str]]:
    """True when HUD markers present and no character-creation markers."""
    if match_character_creation_ocr(ocr_summary):
        return False, []
    hits = match_in_game_hud_ocr(ocr_summary)
    return bool(hits), hits
