"""多语言 Lexicon 工具单测（SC / TC / EN）。"""

from __future__ import annotations

import re

from game_agent.i18n import (
    Concept,
    compile_lexicon_pattern,
    infer_blocked_stage,
    match_phrases_in_text,
    text_contains,
)


def test_text_contains_sc_tc_en_server_select() -> None:
    assert text_contains("请重新选服", Concept.SERVER_SELECT)
    assert text_contains("請重新選服", Concept.SERVER_SELECT)
    assert text_contains("click to select server", Concept.SERVER_SELECT)


def test_text_contains_sc_tc_en_login() -> None:
    assert text_contains("请输入密码", Concept.LOGIN)
    assert text_contains("請輸入密碼", Concept.LOGIN)
    assert text_contains("sign in with account", Concept.LOGIN)


def test_text_contains_sc_tc_en_download() -> None:
    assert text_contains("资源更新中", Concept.RESOURCE_DOWNLOAD)
    assert text_contains("資源更新中", Concept.RESOURCE_DOWNLOAD)
    assert text_contains("downloading assets", Concept.RESOURCE_DOWNLOAD)


def test_compile_lexicon_pattern_english_word_boundary() -> None:
    pat = compile_lexicon_pattern(Concept.LOGIN)
    assert pat.search("please login now")
    assert not pat.search("catalog items")


def test_match_phrases_in_text_hud_english_boundary() -> None:
    hits = match_phrases_in_text("email address login form", Concept.IN_GAME_HUD)
    assert "Mail" not in hits


def test_infer_blocked_stage_traditional_chinese() -> None:
    assert infer_blocked_stage(reason="OCR 選服列表", ui_stage="") == "server_select"
    assert infer_blocked_stage(reason="畫面顯示登入密碼", ui_stage="") == "login"


def test_infer_blocked_stage_english_blob() -> None:
    assert infer_blocked_stage(reason="server select dialog", ui_stage="") == "server_select"
    assert infer_blocked_stage(reason="login password field", ui_stage="") == "login"


def test_infer_blocked_stage_ui_stage_priority() -> None:
    assert infer_blocked_stage(reason="download stalled", ui_stage="login") == "login"


def test_infer_blocked_stage_resource_download_priority() -> None:
    blob = "资源下载 选服"
    assert infer_blocked_stage(reason=blob, ui_stage="") == "resource_download"


def test_character_creation_markers_via_lexicon() -> None:
    hits = match_phrases_in_text("創建角色 選擇職業", Concept.CHARACTER_CREATION, ascii_word_boundary=False)
    assert "創建角色" in hits
    assert "選擇職業" in hits


def test_network_concepts_compile() -> None:
    pat = compile_lexicon_pattern(Concept.NETWORK_ERROR, Concept.CONNECTION_TIMEOUT)
    assert isinstance(pat, re.Pattern)
