"""行为链：多步规划、失败回溯与重规划。"""

from __future__ import annotations

from game_agent.graphs.launch_routing import route_next
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.services.dynamic_route_planner import (
    can_replan_dynamic_chain,
    clear_dynamic_chain,
    get_current_step,
    maybe_build_dynamic_chain,
    record_dynamic_chain_failure,
)
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, x: int, y: int) -> OcrBbox:
    return OcrBbox(text=text, cx=x, cy=y, x1=x - 20, y1=y - 10, x2=x + 20, y2=y + 10)


def test_character_select_builds_behavior_chain_before_adaptive() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    state["privacy_checked"] = True
    state["server_checked"] = True
    facts = LaunchFacts(character_creation_blocking=True)
    bboxes = [
        _bbox("角色 Lv.10", 320, 780),
        _bbox("开始游戏", 820, 1980),
    ]

    assert maybe_build_dynamic_chain(
        state,
        facts,
        bboxes,
        ocr_summary="角色 Lv.10 开始游戏",
    )

    state["facts"] = facts.model_dump()
    assert route_next(state) == "dynamic_action"
    step = get_current_step(state)
    assert step is not None
    assert step.id == "select_character"
    assert step.success_criteria


def test_behavior_chain_records_failure_trace() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    facts = LaunchFacts(character_creation_blocking=True)
    bboxes = [
        _bbox("角色 Lv.10", 320, 780),
        _bbox("开始游戏", 820, 1980),
    ]
    assert maybe_build_dynamic_chain(
        state,
        facts,
        bboxes,
        ocr_summary="角色 Lv.10 开始游戏",
    )
    step = get_current_step(state)
    assert step is not None
    step.attempts = 2

    trace = record_dynamic_chain_failure(
        state,
        step,
        reason="screen fingerprint unchanged",
        ocr_summary="角色 Lv.10 开始游戏",
        artifact="shot.png",
    )

    assert trace.step_id == "select_character"
    assert state["dynamic_last_failed_step_id"] == "select_character"
    assert state["dynamic_replan_count"] == 1
    assert state["dynamic_failure_trace"]
    assert can_replan_dynamic_chain(state, max_replans=2) is True


def test_replan_from_failed_select_character_skips_failed_step() -> None:
    state = empty_launch_graph_state()
    state["login_done"] = True
    facts = LaunchFacts(character_creation_blocking=True)
    bboxes = [
        _bbox("角色 Lv.10", 320, 780),
        _bbox("开始游戏", 820, 1980),
    ]
    assert maybe_build_dynamic_chain(
        state,
        facts,
        bboxes,
        ocr_summary="角色 Lv.10 开始游戏",
    )
    failed = get_current_step(state)
    assert failed is not None
    record_dynamic_chain_failure(
        state,
        failed,
        reason="tap did not select character",
        ocr_summary="角色 Lv.10 开始游戏",
    )
    clear_dynamic_chain(state, failed=False)

    assert maybe_build_dynamic_chain(
        state,
        facts,
        bboxes,
        ocr_summary="角色 Lv.10 开始游戏",
    )
    step = get_current_step(state)
    assert step is not None
    assert step.id == "enter_world"
    assert step.reason.startswith("replan after select_character")
