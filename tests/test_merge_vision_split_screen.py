from __future__ import annotations

from game_agent.graphs.launch_facts import merge_vision_into_facts
from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, cx: int) -> OcrBbox:
    return OcrBbox(text=text, cx=cx, cy=400, x1=cx - 30, y1=100, x2=cx + 30, y2=130)


def test_merge_vision_login_with_split_screen_and_enter_cta() -> None:
    facts = LaunchFacts(
        enter_cta_visible=True,
        login_stage="clear",
        login_blocking=False,
        classify_reason="enter_gate_visible",
    )
    bboxes = [
        _bbox("開始遊戲", 500),
        _bbox("登录", 2200),
        _bbox("忘记密码", 2250),
    ]
    raw = '{"stage": "login", "has_anomaly": false}'
    merged = merge_vision_into_facts(
        facts,
        raw,
        bboxes=bboxes,
        screen_w=2800,
    )
    assert merged.login_blocking is True
    assert merged.login_stage == "login_form"
    assert merged.enter_cta_visible is True


def test_merge_vision_login_suppressed_without_split_when_only_enter_cta() -> None:
    facts = LaunchFacts(
        enter_cta_visible=True,
        login_stage="clear",
        login_blocking=False,
    )
    raw = '{"stage": "login", "has_anomaly": false}'
    merged = merge_vision_into_facts(facts, raw, bboxes=[], screen_w=1080)
    assert merged.login_blocking is False
