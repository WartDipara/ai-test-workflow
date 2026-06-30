"""区服弹窗 OCR 验收与主脑选点。"""

from __future__ import annotations

from game_agent.graphs.launch_routing import route_next
from game_agent.graphs.launch_state_store import mark_tree_node_done, set_server_checked
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.services.server_panel_planner import decide_server_panel_tap_heuristic
from game_agent.services.server_selector_check import (
    ServerSelectorCheckResult,
    evaluate_panel_ocr,
    has_strong_modal_evidence,
    is_page_navigation,
)
from game_agent.services.server_selector_pipeline import (
    _ocr_has_named_server_in_band,
    _slot_empty_for_tap_upgrade,
    finalize_tap_check_result,
)
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, cx: int, cy: int) -> OcrBbox:
    return OcrBbox(text=text, cx=cx, cy=cy, x1=cx - 40, y1=cy - 20, x2=cx + 40, y2=cy + 20)


def _enter_gate() -> OcrBbox:
    return _bbox("进入游戏", 1253, 869)


def test_evaluate_panel_passes_when_enter_occluded_by_modal() -> None:
    """173940：弹窗盖住进入游戏后仍应验收通过。"""
    enter = _enter_gate()
    before = [
        enter,
        _bbox("迢迢暗度", 900, 520),
        _bbox("点击选区", 1100, 520),
    ]
    after = [
        _bbox("选择服务器", 1200, 280),
        _bbox("推荐", 400, 350),
        _bbox("五十区", 400, 450),
        _bbox("迢迢暗度", 900, 500),
        _bbox("飞星传恨", 1200, 500),
    ]
    verdict = evaluate_panel_ocr(before, after, enter)
    assert verdict.passed is True
    assert verdict.evidence in ("modal_title", "modal_category", "modal_rows", "close_plus_new_rows")
    assert verdict.page_navigation is False


def test_is_page_navigation_false_when_modal_covers_enter() -> None:
    enter = _enter_gate()
    before = [enter, _bbox("点击选区", 1100, 520)]
    after = [
        _bbox("选择服务器", 1200, 280),
        _bbox("五十区", 400, 450),
    ]
    assert is_page_navigation(before, after, enter) is False


def test_has_strong_modal_evidence_on_server_list() -> None:
    enter = _enter_gate()
    after = [
        _bbox("选择服务器", 1200, 280),
        _bbox("五十区", 400, 450),
        _bbox("迢迢暗度", 900, 500),
    ]
    assert has_strong_modal_evidence(after, enter) is True


def test_finalize_skips_e2006_when_modal_evidence_on_failure() -> None:
    enter = _enter_gate()
    after = [_bbox("选择服务器", 1200, 280), _bbox("五十区", 400, 450)]
    tap_fail = ServerSelectorCheckResult(
        ok=False,
        message="[ServerCheck] FAILED verify",
        taps_used=3,
        panel_opened=True,
    )
    out = finalize_tap_check_result(
        probe_msg="",
        probe=None,
        tap_result=tap_fail,
        slot_empty=True,
        last_after_bboxes=after,
        enter_bbox=enter,
    )
    assert "FAILED [E2006]" not in out.message
    assert out.panel_opened is True


def test_slot_empty_false_when_named_server_in_band() -> None:
    enter = _enter_gate()
    bboxes = [
        enter,
        _bbox("迢迢暗度", 1250, 720),
        _bbox("点击选区", 1250, 780),
    ]
    assert _ocr_has_named_server_in_band(bboxes, enter, 2400, 1080) is True
    assert _slot_empty_for_tap_upgrade(None, bboxes, enter, 2400, 1080) is False


def test_heuristic_close_prefers_dialog_top_right() -> None:
    bboxes = [
        _bbox("选择服务器", 1200, 280),
        _bbox("五十区", 400, 450),
        _bbox("迢迢暗度", 900, 500),
    ]
    decision = decide_server_panel_tap_heuristic(
        bboxes, screen_w=2400, screen_h=1080, prefer_close=True
    )
    assert decision is not None
    assert decision.intent == "close_panel"
    assert decision.x > 2000


def test_route_tap_enter_after_server_checked() -> None:
    state = empty_launch_graph_state()
    state["facts"] = LaunchFacts(
        server_slot_visible=True,
        enter_cta_visible=True,
    ).model_dump()
    state["login_done"] = True
    state["privacy_checked"] = True
    mark_tree_node_done(state, "check_server_selector")
    set_server_checked(state)
    assert route_next(state) == "tap_enter_game"
