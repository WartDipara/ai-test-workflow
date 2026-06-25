"""ActionFrame 节点内自省测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from game_agent.graphs.launch_facts import merge_sticky_gate_facts
from game_agent.graphs.launch_phase import reconcile_action_frames
from game_agent.graphs.launch_routing import plan_route
from game_agent.graphs.launch_state_store import mark_tree_node_done
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.services.action_correction import apply_correction
from game_agent.services.action_frame import run_action_frame
from game_agent.services.action_reflection import reflect_on_failure
from game_agent.services.download_gate import ocr_has_download_context
from game_agent.services.node_verifier import NodeVerifyResult, verify_stage_exit
from game_agent.services.privacy_gate import ocr_has_privacy_context


def _state(**kwargs):
    state = empty_launch_graph_state()
    state.update(kwargs)
    if isinstance(state.get("facts"), LaunchFacts):
        state["facts"] = state["facts"].model_dump()
    return state


def test_ocr_update_date_does_not_trigger_download() -> None:
    ocr = "适龄提示和隐私政策 更新日期 2024年1月1日 同意"
    assert ocr_has_privacy_context(ocr)
    assert not ocr_has_download_context(ocr)


def test_real_download_still_detected() -> None:
    ocr = "资源更新中 35% 下载"
    assert ocr_has_download_context(ocr)


def test_verify_privacy_modal_pass_when_consent_buttons_disappear() -> None:
    before = "适龄提示 隐私政策 不同意 同意"
    after = "适龄提示 隐私政策 更新日期 2024年1月1日"
    result = verify_stage_exit(
        ocr_before=before,
        ocr_after=after,
        expected_stage="privacy_modal",
    )
    assert result.passed is True


def test_verify_privacy_modal_fail_when_buttons_remain() -> None:
    before = "适龄提示 隐私政策 不同意 同意"
    after = "适龄提示 隐私政策 不同意 同意"
    result = verify_stage_exit(
        ocr_before=before,
        ocr_after=after,
        expected_stage="privacy_modal",
    )
    assert result.passed is False


def test_reflect_privacy_modal_wrong_coords() -> None:
    verify = NodeVerifyResult(passed=False, reason="privacy modal consent row still visible")
    reflection = reflect_on_failure(
        node="handle_initial_privacy_dialog",
        verify=verify,
        ocr_before="适龄提示 不同意 同意",
        ocr_after="适龄提示 不同意 同意",
        facts=LaunchFacts(agree_button_xy=(704, 1583)),
        expected_stage="privacy_modal",
    )
    assert reflection.root_cause == "wrong_coords"
    assert reflection.retry_coords == (704, 1583)


def test_privacy_modal_still_open() -> None:
    from game_agent.services.privacy_gate import privacy_modal_still_open

    assert privacy_modal_still_open("适龄提示 隐私政策 不同意 同意")
    assert not privacy_modal_still_open("适龄提示 隐私政策 更新日期")
    assert not privacy_modal_still_open("账号 密码 登录")


def test_reflect_wrong_route_on_download_node_with_privacy_screen() -> None:
    verify = NodeVerifyResult(passed=False, reason="privacy modal still visible")
    reflection = reflect_on_failure(
        node="handle_download",
        verify=verify,
        ocr_before="适龄提示 隐私政策",
        ocr_after="适龄提示 隐私政策 同意",
        facts=LaunchFacts(download_visible=True),
        expected_stage="download",
    )
    assert reflection.root_cause == "wrong_route"
    assert reflection.fact_patches.get("download_visible") is False
    assert reflection.fact_patches.get("initial_privacy_dialog") is True


def test_merge_sticky_gate_facts_preserves_privacy_modal() -> None:
    prev = LaunchFacts(
        initial_privacy_dialog=True,
        agree_button_xy=(620, 1585),
        privacy_gate_kind="modal",
    )
    fresh = LaunchFacts(
        download_visible=True,
        classify_reason="privacy_context_detected",
    )
    state = _state()
    merged = merge_sticky_gate_facts(fresh, prev_facts=prev, state=state)
    assert merged.initial_privacy_dialog is True
    assert merged.agree_button_xy == (620, 1585)
    assert merged.download_visible is False


def test_reconcile_action_frames_restores_privacy_routing() -> None:
    facts = LaunchFacts(
        download_visible=True,
        initial_privacy_dialog=False,
    )
    state = _state(
        facts=facts.model_dump(),
        last_reflection={
            "root_cause": "wrong_route",
            "recover_hint": "restore privacy milestone routing",
            "fact_patches": {
                "download_visible": False,
                "initial_privacy_dialog": True,
                "agree_button_xy": (620, 1585),
                "privacy_gate_kind": "modal",
            },
        },
    )
    merged = reconcile_action_frames(state, facts)
    assert merged.initial_privacy_dialog is True
    assert merged.download_visible is False
    assert merged.agree_button_xy == (620, 1585)
    assert state.get("last_reflection") == {}


def test_wrong_route_correction_routes_to_privacy_milestone() -> None:
    facts = LaunchFacts(
        download_visible=True,
        initial_privacy_dialog=False,
        agree_button_xy=(620, 1585),
    )
    state = _state(facts=facts.model_dump())
    reflection = reflect_on_failure(
        node="handle_download",
        verify=NodeVerifyResult(passed=False, reason="privacy modal still visible"),
        ocr_before="适龄提示",
        ocr_after="适龄提示 同意",
        facts=facts,
        expected_stage="download",
    )
    apply_correction(state, reflection)
    assert plan_route(state) == "handle_initial_privacy_dialog"


def test_run_action_frame_passes_on_verify(tmp_path: Path) -> None:
    adb = MagicMock()
    adb.device_serial = "test"
    adb.tap.return_value = "Tapped"
    adb.wait_seconds.return_value = "waited"
    adb.screencap_png = MagicMock()

    state = _state(last_ocr_summary="隐私政策 同意")

    async def act(st, attempt):
        return adb.tap(1, 2, width=1080, height=2400)

    def verify(st, before, after):
        if after == "after":
            return NodeVerifyResult(passed=True, reason="privacy cleared")
        return NodeVerifyResult(passed=False, reason="still visible")

    from game_agent.services import action_frame as af
    from game_agent.services.action_frame import ObserveCapture
    from game_agent.utils.ocr_util import OcrBbox

    async def fake_observe(*args, **kwargs):
        return ObserveCapture(
            screenshot=str(tmp_path / "shot.png"),
            ocr_summary="after",
            bboxes=[OcrBbox(text="登录", cx=1, cy=1, x1=0, y1=0, x2=0, y2=0)],
        )

    original = af.capture_observe
    af.capture_observe = fake_observe
    try:
        result = asyncio.run(
            run_action_frame(
                state,
                node="handle_initial_privacy_dialog",
                adb=adb,
                artifact_root=tmp_path,
                screen_w=1080,
                screen_h=2400,
                act_fn=act,
                verify_fn=verify,
                max_attempts=2,
                ocr_before="隐私政策 同意",
                expected_stage="privacy_modal",
            ),
        )
    finally:
        af.capture_observe = original

    assert result.passed is True
    assert result.attempts == 1


def test_sticky_merge_skipped_when_privacy_milestone_done() -> None:
    prev = LaunchFacts(initial_privacy_dialog=True, agree_button_xy=(1, 2))
    fresh = LaunchFacts(initial_privacy_dialog=False)
    state = _state()
    mark_tree_node_done(state, "handle_initial_privacy_dialog")
    state["privacy_checked"] = True
    merged = merge_sticky_gate_facts(fresh, prev_facts=prev, state=state)
    assert merged.initial_privacy_dialog is False
