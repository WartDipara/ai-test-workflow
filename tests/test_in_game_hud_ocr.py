"""局内 HUD OCR 词表。"""

from __future__ import annotations

from game_agent.utils.in_game_hud_ocr import (
    match_in_game_hud_ocr,
    should_trigger_in_game_hud_check,
)


def test_match_in_game_hud_ocr() -> None:
    hits = match_in_game_hud_ocr("主界面 背包 技能 123,456 商城")
    assert "背包" in hits
    assert "技能" in hits
    assert "商城" in hits


def test_should_trigger_blocks_character_creation() -> None:
    ocr = "创建角色 背包 技能"
    ok, hits = should_trigger_in_game_hud_check(ocr)
    assert not ok
    assert hits == []


def test_should_trigger_with_hud_only() -> None:
    ok, hits = should_trigger_in_game_hud_check("任务列表 Inventory Shop")
    assert ok
    assert "任务" in hits
    assert "Inventory" in hits


def test_match_english_hud_role_mall_case_insensitive() -> None:
    hits = match_in_game_hud_ocr("role  skills  guild  mall  forging")
    assert "Role" in hits
    assert "Skills" in hits
    assert "Guild" in hits
    assert "Mall" in hits
    assert "Forging" in hits
