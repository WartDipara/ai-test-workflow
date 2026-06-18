"""Gate 化主流程路由与门禁测试。"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from game_agent.graphs.launch_facts import (
    classify_screen_facts,
    needs_async_vision_enrichment,
    needs_sync_interpretation,
)
from game_agent.graphs.launch_nodes import handle_download_node
from game_agent.graphs.launch_routing import plan_route, should_route_scene
from game_agent.graphs.launch_state_store import completed_tree_node, mark_tree_node_done
from game_agent.graphs.static_priority import blocks_scene_routing, has_pending_static_work
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.models.scene import SceneClassification, SceneTransition
from game_agent.services.download_gate import (
    apply_download_ocr_to_facts,
    ocr_has_clear_download_progress,
    ocr_still_downloading,
)
from game_agent.services.login_stage_probe import probe_login_stage
from game_agent.services.privacy_gate import should_invoke_privacy_gate_vlm
from game_agent.services.scene_classifier import classify_scene
from game_agent.services.scene_strategies import should_deactivate_scene_strategy
from game_agent.services.sub_account_gate import should_invoke_sub_account_gate_vlm
from game_agent.utils.ocr_util import OcrBbox, serialize_bboxes


def _state(**kwargs):
    state = empty_launch_graph_state()
    state.update(kwargs)
    if isinstance(state.get("facts"), LaunchFacts):
        state["facts"] = state["facts"].model_dump()
    return state


def test_sub_account_fullscreen_ocr_routes_select_sub_account_not_scene() -> None:
    bboxes = [
        OcrBbox(text="选择小号", cx=540, cy=400, x1=0, y1=0, x2=0, y2=0),
        OcrBbox(text="上次登录", cx=540, cy=900, x1=0, y1=0, x2=0, y2=0),
        OcrBbox(text="默认", cx=540, cy=950, x1=0, y1=0, x2=0, y2=0),
    ]
    ocr = "选择小号 上次登录 默认"
    probe = probe_login_stage(bboxes, screen_w=1080, screen_h=2400)
    assert probe.stage == "sub_account_select"
    assert probe.blocking is True
    assert probe.action_xy is not None

    state = _state(
        login_done=True,
        sub_account_selected=False,
        last_ocr_summary=ocr,
        last_bboxes=serialize_bboxes(bboxes),
    )
    facts = classify_screen_facts(bboxes, screen_w=1080, screen_h=2400, ocr_summary=ocr)
    state["facts"] = facts.model_dump()
    state["scene_id"] = "loading"
    state["scene_confidence"] = 0.9

    assert has_pending_static_work(state, facts) is True
    assert blocks_scene_routing(state, facts) is True
    assert should_route_scene(state, facts) is False
    assert plan_route(state) == "select_sub_account"


def test_download_progress_routes_handle_download_not_scene() -> None:
    ocr = "资源更新中 35% 下载"
    facts = apply_download_ocr_to_facts(LaunchFacts(), ocr_merged=ocr)
    assert facts.download_visible is True
    assert facts.download_progress_text == "35%"
    assert ocr_has_clear_download_progress(ocr) is True

    state = _state(
        login_done=True,
        privacy_checked=True,
        sub_account_selected=True,
        last_ocr_summary=ocr,
    )
    state["facts"] = facts.model_dump()
    state["scene_id"] = "loading"
    state["scene_confidence"] = 0.9

    assert should_route_scene(state, facts) is False
    assert plan_route(state) == "handle_download"


def test_download_visible_not_classified_as_loading_scene() -> None:
    facts = LaunchFacts(download_visible=True)
    cls = classify_scene(
        facts,
        [],
        ocr_summary="下载 35%",
        screen_w=1080,
        screen_h=2400,
    )
    assert cls.scene_id != "loading"


def test_download_in_progress_does_not_mark_done() -> None:
    @dataclass
    class _Deps:
        screen_width: int = 1080
        screen_height: int = 2400
        adb: MagicMock = MagicMock()

    deps = _Deps()
    deps.adb.wait_seconds = MagicMock()

    state = _state(
        last_ocr_summary="资源下载 45%",
        last_bboxes=[],
    )
    mark_tree_node_done(state, "atomic_login")
    assert completed_tree_node(state, "handle_download") is False

    import asyncio

    result = asyncio.run(handle_download_node(state, deps))  # type: ignore[arg-type]
    assert completed_tree_node(result, "handle_download") is False


def test_download_ui_gone_marks_done() -> None:
    @dataclass
    class _Deps:
        screen_width: int = 1080
        screen_height: int = 2400
        adb: MagicMock = MagicMock()

    deps = _Deps()
    deps.adb.wait_seconds = MagicMock()

    state = _state(last_ocr_summary="进入游戏")
    assert ocr_still_downloading("进入游戏") is False

    import asyncio

    result = asyncio.run(handle_download_node(state, deps))  # type: ignore[arg-type]
    assert completed_tree_node(result, "handle_download") is True


def test_download_visible_skips_async_vlm() -> None:
    facts = LaunchFacts(download_visible=True, download_progress_text="35%")
    assert needs_async_vision_enrichment(facts) is False


def test_sub_account_coords_skip_sync_vlm() -> None:
    facts = LaunchFacts(
        sub_account_blocking=True,
        sub_account_action_xy=(540, 900),
        login_stage="sub_account_select",
    )
    assert needs_sync_interpretation(facts, ocr_merged="选择小号 上次登录") is False
    assert should_invoke_sub_account_gate_vlm(facts, ocr_merged="选择小号") is False


def test_checkbox_gate_skips_sync_interpretation_even_with_character_hint() -> None:
    facts = LaunchFacts(
        terms_checkbox_visible=True,
        character_creation_blocking=True,
        login_blocking=True,
        login_stage="login_form",
    )
    assert needs_sync_interpretation(facts, ocr_merged="用户协议 隐私政策 LV.") is False


def test_privacy_gate_skips_when_download_visible() -> None:
    facts = LaunchFacts(download_visible=True)
    ocr = "已阅读并同意 用户协议 隐私政策"
    assert should_invoke_privacy_gate_vlm(facts, ocr_merged=ocr) is False


def test_true_loading_black_screen_routes_scene_wait() -> None:
    facts = LaunchFacts()
    cls = SceneClassification(scene_id="loading", confidence=0.9, evidence="black")
    state = _state(
        login_done=True,
        privacy_checked=True,
        sub_account_selected=True,
        scene_id="loading",
        scene_confidence=0.9,
        scene_strategy_active=True,
        active_scene_strategy="loading",
        facts=facts.model_dump(),
    )
    transition = SceneTransition(kind="animation_or_loading", reason="black")
    assert should_deactivate_scene_strategy(state, cls, facts, transition) is False
    assert should_route_scene(state, facts) is True
    assert plan_route(state) == "scene_action"


def test_static_pending_clears_active_loading_strategy() -> None:
    facts = LaunchFacts(download_visible=True)
    cls = SceneClassification(scene_id="loading", confidence=0.9, evidence="was_loading")
    state = _state(
        login_done=True,
        scene_strategy_active=True,
        active_scene_strategy="loading",
        facts=facts.model_dump(),
    )
    transition = SceneTransition(kind="none", reason="download_detected")
    assert should_deactivate_scene_strategy(state, cls, facts, transition) is True
    assert state.get("scene_strategy_active") is False
