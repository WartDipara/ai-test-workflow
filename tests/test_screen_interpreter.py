"""ScreenInterpreter schema 与 facts 合并。"""

from __future__ import annotations

from game_agent.graphs.launch_facts import (
    merge_interpretation_into_facts,
    needs_sync_interpretation,
)
from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.models.screen_interpretation import (
    ScreenInterpretation,
    TapTarget,
    parse_interpretation_json,
)


def test_parse_interpretation_json() -> None:
    raw = """
    {"stage": "sub_account_select", "blocking": true,
     "tap_target": {"x": 800, "y": 300, "label": "小号1"},
     "completion_signals": ["选服"], "reason": "picker visible"}
    """
    interp = parse_interpretation_json(raw)
    assert interp.stage == "sub_account_select"
    assert interp.tap_target is not None
    assert interp.tap_target.x == 800


def test_merge_interpretation_fills_sub_account_tap() -> None:
    facts = LaunchFacts(
        sub_account_blocking=True,
        login_stage="sub_account_select",
        sub_account_action_xy=None,
    )
    interp = ScreenInterpretation(
        stage="sub_account_select",
        blocking=True,
        tap_target=TapTarget(x=900, y=400, label="小号1"),
        completion_signals=["踏入仙途"],
    )
    merged = merge_interpretation_into_facts(facts, interp)
    assert merged.sub_account_action_xy == (900, 400)
    assert merged.screen_completion_signals == ["踏入仙途"]


def test_merge_preserves_ocr_coords() -> None:
    facts = LaunchFacts(
        sub_account_blocking=True,
        sub_account_action_xy=(100, 200),
    )
    interp = ScreenInterpretation(
        stage="sub_account_select",
        blocking=True,
        tap_target=TapTarget(x=900, y=400, label="other"),
    )
    merged = merge_interpretation_into_facts(
        facts,
        interp,
        ocr_has_sub_account_coords=True,
    )
    assert merged.sub_account_action_xy == (100, 200)


def test_needs_sync_when_blocking_no_coords() -> None:
    facts = LaunchFacts(sub_account_blocking=True, sub_account_action_xy=None)
    assert needs_sync_interpretation(facts) is True


def test_merge_announcement_and_character_creation() -> None:
    ann = merge_interpretation_into_facts(
        LaunchFacts(),
        ScreenInterpretation(
            stage="announcement",
            blocking=True,
            tap_target=TapTarget(x=100, y=50, label="关闭"),
        ),
    )
    assert ann.announcement_overlay is True
    assert ann.announcement_dismiss_xy == (100, 50)

    cc = merge_interpretation_into_facts(
        LaunchFacts(),
        ScreenInterpretation(stage="character_creation", blocking=True),
    )
    assert cc.character_creation_blocking is True
