"""小号凭据目标 + OCR 选点。"""

from __future__ import annotations

from game_agent.services.credentials import (
    GameCredentials,
    resolve_sub_account_match_phrases,
)
from game_agent.services.sub_account_locator import (
    normalize_sub_account_compare,
    pick_sub_account_bbox,
)
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, cx: int, cy: int) -> OcrBbox:
    return OcrBbox(text=text, cx=cx, cy=cy, x1=cx - 40, y1=cy - 20, x2=cx + 40, y2=cy + 20)


def test_resolve_default_phrases_when_sub_account_unset() -> None:
    cred = GameCredentials(username="u", password="p")
    phrases = resolve_sub_account_match_phrases(cred)
    assert "小号1" in phrases
    assert "sub-account 1" in phrases


def test_resolve_explicit_sub_account_phrases() -> None:
    cred = GameCredentials(username="u", password="p", sub_account="小号2")
    phrases = resolve_sub_account_match_phrases(cred)
    assert phrases[0] == "小号2"


def test_pick_sub_account_小号1_not_小号说明() -> None:
    bboxes = [
        _bbox("小号说明", 540, 200),
        _bbox("小号1", 540, 600),
    ]
    picked = pick_sub_account_bbox(bboxes, target_phrases=("小号1",), screen_w=1080)
    assert picked is not None
    assert picked.text == "小号1"


def test_pick_sub_account_english_case_insensitive() -> None:
    cred = GameCredentials(username="u", password="p", sub_account="sub-account 1")
    phrases = resolve_sub_account_match_phrases(cred)
    picked = pick_sub_account_bbox(
        bboxes=[_bbox("SUB-ACCOUNT 1", 800, 500)],
        target_phrases=phrases,
        screen_w=1080,
    )
    assert picked is not None
    assert picked.text == "SUB-ACCOUNT 1"


def test_pick_sub_account_sub_account_1_variant() -> None:
    picked = pick_sub_account_bbox(
        bboxes=[_bbox("Sub-Account 1", 800, 500)],
        target_phrases=("sub-account 1",),
        screen_w=1080,
    )
    assert picked is not None


def test_pick_sub_account_wrong_target_returns_none() -> None:
    picked = pick_sub_account_bbox(
        bboxes=[_bbox("小号1", 540, 400)],
        target_phrases=("小号2",),
        screen_w=1080,
    )
    assert picked is None


def test_normalize_sub_account_compare_casefold() -> None:
    assert normalize_sub_account_compare("Sub-Account 1") == normalize_sub_account_compare("SUB-ACCOUNT 1")


def test_probe_login_stage_uses_credential_target() -> None:
    from game_agent.services.login_stage_probe import probe_login_stage

    bboxes = [
        _bbox("选择小号", 800, 100),
        _bbox("小号说明", 800, 200),
        _bbox("小号1", 800, 500),
    ]
    probe = probe_login_stage(
        bboxes,
        screen_w=1080,
        screen_h=2400,
        sub_account_phrases=("小号1",),
    )
    assert probe.stage == "sub_account_select"
    assert probe.action_xy == (800, 500)
    assert probe.action_label == "小号1"
