"""Text matching utilities over i18n lexicon."""

from __future__ import annotations

import re
from collections.abc import Sequence

from game_agent.i18n.lexicon import PHRASES, Concept, Locale, PhraseSet

_ASCII_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\s'\-/]*$")


def normalize_match_blob(text: str) -> str:
    """Lowercase + normalize full-width space for reason/ui_progress matching."""
    blob = (text or "").replace("\u3000", " ").strip().lower()
    return blob


def all_phrases(
    concept: Concept,
    *,
    locales: Sequence[Locale] | None = None,
) -> tuple[str, ...]:
    ps = PHRASES[concept]
    if locales is None:
        return ps.all_locales()
    parts: list[str] = []
    seen: set[str] = set()
    for loc in locales:
        bucket = getattr(ps, loc, ())
        for phrase in bucket:
            if phrase not in seen:
                seen.add(phrase)
                parts.append(phrase)
    for phrase in ps.variants:
        if phrase not in seen:
            seen.add(phrase)
            parts.append(phrase)
    return tuple(parts)


def phrase_in_text(text: str, phrase: str, *, ascii_word_boundary: bool = True) -> bool:
    if not phrase or not text:
        return False
    if ascii_word_boundary and phrase.isascii() and _ASCII_WORD.match(phrase):
        return re.search(rf"\b{re.escape(phrase)}\b", text, re.IGNORECASE) is not None
    return phrase in text


def text_contains(text: str, *concepts: Concept) -> bool:
    return first_matching_concept(text, concepts) is not None


def first_matching_concept(text: str, concepts: Sequence[Concept]) -> Concept | None:
    if not text:
        return None
    for concept in concepts:
        if match_phrases_in_text(text, concept):
            return concept
    return None


def match_phrases_in_text(
    text: str,
    concept: Concept,
    *,
    locales: Sequence[Locale] | None = None,
    ascii_word_boundary: bool | None = None,
) -> list[str]:
    """Return matched phrases for a concept (deduped, stable order)."""
    if not text:
        return []
    use_boundary = True if ascii_word_boundary is None else ascii_word_boundary
    if concept == Concept.IN_GAME_HUD:
        use_boundary = True
    seen: set[str] = set()
    matched: list[str] = []
    for phrase in all_phrases(concept, locales=locales):
        if phrase_in_text(text, phrase, ascii_word_boundary=use_boundary) and phrase not in seen:
            seen.add(phrase)
            matched.append(phrase)
    return matched


def _pattern_part(phrase: str) -> str:
    if phrase.isascii() and _ASCII_WORD.match(phrase.strip()):
        return rf"\b{re.escape(phrase.strip())}\b"
    return re.escape(phrase)


def compile_lexicon_pattern(
    *concepts: Concept,
    flags: int = re.IGNORECASE,
) -> re.Pattern[str]:
    """Build OR-regex from all phrases of the given concepts."""
    parts: list[str] = []
    seen: set[str] = set()
    for concept in concepts:
        for phrase in all_phrases(concept):
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            parts.append(_pattern_part(phrase))
    if not parts:
        return re.compile(r"(?!x)x", flags)
    return re.compile("|".join(parts), flags)


def is_network_anomaly_text(text: str) -> bool:
    """True when text matches network/download/region error lexicon (SC/TC/EN)."""
    return text_contains(
        text or "",
        Concept.NETWORK_ERROR,
        Concept.DOWNLOAD_FAILED,
        Concept.REGION_RESTRICTED,
        Concept.SERVER_BUSY,
        Concept.CONNECTION_FAILED,
        Concept.CONNECTION_TIMEOUT,
    )


def first_network_anomaly_phrase(text: str) -> str:
    for concept in (
        Concept.NETWORK_ERROR,
        Concept.DOWNLOAD_FAILED,
        Concept.CONNECTION_TIMEOUT,
        Concept.CONNECTION_FAILED,
        Concept.SERVER_BUSY,
        Concept.REGION_RESTRICTED,
    ):
        hits = match_phrases_in_text(text, concept, ascii_word_boundary=False)
        if hits:
            return hits[0]
    return ""


def compile_phrase_set_pattern(ps: PhraseSet, *, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    parts: list[str] = []
    seen: set[str] = set()
    for phrase in ps.all_locales():
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(_pattern_part(phrase))
    if not parts:
        return re.compile(r"(?!x)x", flags)
    return re.compile("|".join(parts), flags)
