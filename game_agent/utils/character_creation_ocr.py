from __future__ import annotations

# 命中任一词则视为仍在创角/局外流程，不得判为已进入游戏（与按键精灵脚本无关）。
CHARACTER_CREATION_OCR_MARKERS: tuple[str, ...] = (
    "创建角色",
    "新建角色",
    "角色创建",
    "选择角色",
    "选择职业",
    "取名",
    "输入名字",
    "输入名称",
    "捏脸",
    "外观设定",
    "性别选择",
    "开始冒险",
    "Create Character",
    "Createcharacter",
    "Select Class",
    "Name your character",
    "Choose Character",
    "Character Creation",
)


def match_character_creation_ocr(ocr_summary: str) -> list[str]:
    """返回 OCR 文本中命中的创角相关关键词（去重、保持顺序）。"""
    text = ocr_summary or ""
    seen: set[str] = set()
    matched: list[str] = []
    for marker in CHARACTER_CREATION_OCR_MARKERS:
        if marker in text and marker not in seen:
            seen.add(marker)
            matched.append(marker)
    return matched
