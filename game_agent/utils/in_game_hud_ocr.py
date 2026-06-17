from __future__ import annotations

from game_agent.utils.character_creation_ocr import match_character_creation_ocr

# 命中任一词则视为可能出现局内 HUD，可触发补充进游戏确认（仍需多模态）。
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
    "Inventory",
    "Backpack",
    "Skill",
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
)


def match_in_game_hud_ocr(ocr_summary: str) -> list[str]:
    """返回 OCR 文本中命中的局内 HUD 关键词（去重、保持顺序）。"""
    text = ocr_summary or ""
    seen: set[str] = set()
    matched: list[str] = []
    for marker in IN_GAME_HUD_OCR_MARKERS:
        if marker in text and marker not in seen:
            seen.add(marker)
            matched.append(marker)
    return matched


def should_trigger_in_game_hud_check(ocr_summary: str) -> tuple[bool, list[str]]:
    """有局内 HUD 命中且无创角 OCR 时返回 True。"""
    if match_character_creation_ocr(ocr_summary):
        return False, []
    hits = match_in_game_hud_ocr(ocr_summary)
    return bool(hits), hits
