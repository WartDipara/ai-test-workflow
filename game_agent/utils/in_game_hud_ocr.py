from __future__ import annotations

from game_agent.utils.character_creation_ocr import match_character_creation_ocr

# HUD markers: match any → may trigger in-game confirmation (still needs multimodal).
IN_GAME_HUD_OCR_MARKERS: tuple[str, ...] = (
    "商城",
    "背包",
    "技能",
    "任务",
    "地图",
    "组队",
    "邮件",
    "设置",
    "好友",
    "公会",
    "装备",
    "属性",
    "成就",
    "活动",
    "商店",
    "锻造",
    "Inventory",
    "Backpack",
    "Skill",
    "Skills",
    "Quest",
    "Map",
    "Team",
    "Mail",
    "Settings",
    "Friends",
    "Guild",
    "Equipment",
    "Shop",
    "Store",
    "Mall",
    "Role",
    "Party",
    "Bonuses",
    "Forging",
    "Craft",
)


def _marker_in_text(text: str, marker: str) -> bool:
    if marker.isascii():
        return marker.lower() in text.lower()
    return marker in text


def match_in_game_hud_ocr(ocr_summary: str) -> list[str]:
    """Return matched in-game HUD keywords (deduped, stable order)."""
    text = ocr_summary or ""
    seen: set[str] = set()
    matched: list[str] = []
    for marker in IN_GAME_HUD_OCR_MARKERS:
        if _marker_in_text(text, marker) and marker not in seen:
            seen.add(marker)
            matched.append(marker)
    return matched


def should_trigger_in_game_hud_check(ocr_summary: str) -> tuple[bool, list[str]]:
    """True when HUD markers hit and no character-creation OCR is present."""
    if match_character_creation_ocr(ocr_summary):
        return False, []
    hits = match_in_game_hud_ocr(ocr_summary)
    return bool(hits), hits
