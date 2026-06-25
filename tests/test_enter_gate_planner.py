"""进游戏门主脑/启发式选点。"""

from __future__ import annotations

from game_agent.services.enter_gate_planner import (
    decide_enter_gate_tap_heuristic,
    enter_gate_likely_visible,
)
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, cx: int, cy: int) -> OcrBbox:
    return OcrBbox(text=text, cx=cx, cy=cy, x1=cx - 40, y1=cy - 20, x2=cx + 40, y2=cy + 20)


def test_heuristic_picks_enter_game_not_health_footer() -> None:
    bboxes = [
        _bbox("进入游戏", 1253, 869),
        _bbox("接受游戏中存在PK玩法", 1276, 978),
        _bbox("本游戏适合16岁以上玩家进入", 508, 1044),
    ]
    decision = decide_enter_gate_tap_heuristic(bboxes, screen_h=1080)
    assert decision is not None
    assert decision.x == 1253
    assert decision.y == 869
    assert "进入游戏" in decision.target_text


def test_enter_gate_likely_visible_with_primary_cta() -> None:
    bboxes = [_bbox("进入游戏", 1253, 869)]
    assert enter_gate_likely_visible(bboxes) is True


def test_heuristic_skips_health_only_footer() -> None:
    bboxes = [_bbox("本游戏适合16岁以上玩家进入", 508, 1044)]
    assert decide_enter_gate_tap_heuristic(bboxes, screen_h=1080) is None
    assert enter_gate_likely_visible(bboxes) is False
