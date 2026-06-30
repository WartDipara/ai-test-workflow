"""bbox_for_text 歧义与严格匹配单测。"""

from __future__ import annotations

from game_agent.services.behavior_chain import bbox_for_text, bbox_for_text_strict
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, *, cx: int, cy: int) -> OcrBbox:
    return OcrBbox(text=text, x1=cx - 10, y1=cy - 10, x2=cx + 10, y2=cy + 10, cx=cx, cy=cy)


def test_battle_vs_assist_ambiguity_with_prefer_xy() -> None:
    bboxes = [
        _bbox("助战", cx=243, cy=345),
        _bbox("战斗", cx=554, cy=2328),
    ]
    hit = bbox_for_text(bboxes, "战", prefer_xy=(554, 2328))
    assert hit is not None
    assert hit.text == "战斗"
    assert hit.cx == 554


def test_full_label_exact_match() -> None:
    bboxes = [
        _bbox("助战", cx=243, cy=345),
        _bbox("战斗", cx=554, cy=2328),
    ]
    hit = bbox_for_text_strict(bboxes, "战斗")
    assert hit is not None
    assert hit.cx == 554


def test_single_char_without_prefer_no_assist_substring() -> None:
    bboxes = [
        _bbox("助战", cx=243, cy=345),
        _bbox("战斗", cx=554, cy=2328),
    ]
    assert bbox_for_text_strict(bboxes, "战") is None


def test_longest_substring_preference() -> None:
    bboxes = [
        _bbox("开始", cx=100, cy=2000),
        _bbox("开始游戏", cx=540, cy=2200),
    ]
    hit = bbox_for_text(bboxes, "开始游戏")
    assert hit is not None
    assert hit.cx == 540
