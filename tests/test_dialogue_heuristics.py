"""对话启发式单元测试。"""

from __future__ import annotations

from game_agent.services.dialogue_heuristics import (
    pick_dialogue_advance_bbox,
    score_dialogue_from_bboxes,
)
from game_agent.utils.ocr_util import OcrBbox


def test_single_bottom_line_scores_as_dialogue() -> None:
    bboxes = [
        OcrBbox(
            text="少年，你可愿随我修行？",
            cx=540,
            cy=2050,
            x1=100,
            y1=2020,
            x2=980,
            y2=2080,
        ),
    ]
    score, ev = score_dialogue_from_bboxes(bboxes, screen_h=2400)
    assert score >= 0.55
    assert "narrative_line" in ev


def test_pick_longest_narrative_line_for_tap() -> None:
    bboxes = [
        OcrBbox(text="云裳", cx=800, cy=1900, x1=750, y1=1880, x2=850, y2=1920),
        OcrBbox(
            text="这把剑?这似乎是……轩辕剑!",
            cx=313,
            cy=2028,
            x1=50,
            y1=1990,
            x2=900,
            y2=2060,
        ),
    ]
    picked = pick_dialogue_advance_bbox(bboxes, screen_h=2400)
    assert picked is not None
    assert "轩辕剑" in picked.text
    assert picked.cx == 313
    assert picked.cy == 2028
