"""区服选择 pipeline：ready 短路、executor 种子开关。"""

from __future__ import annotations

from game_agent.graphs.launch_limits import seed_launch_graph_executor_flags
from game_agent.models.launch_graph_state import empty_launch_graph_state
from game_agent.models.server_connectivity_probe import ServerConnectivityProbe
from game_agent.models.settings import AppConfig
from game_agent.services.server_selector_pipeline import (
    _probe_indicates_ready_skip,
    _ready_skip_result,
)


def test_probe_indicates_ready_skip() -> None:
    probe = ServerConnectivityProbe(
        on_enter_game_screen=True,
        enter_button_visible=True,
        server_slot_status="ready",
        recommendation="tap_verify",
        confidence=0.95,
        reason="valid server",
    )
    assert _probe_indicates_ready_skip(probe) is True


def test_probe_ready_skip_false_when_overlay() -> None:
    probe = ServerConnectivityProbe(
        on_enter_game_screen=True,
        enter_button_visible=True,
        server_slot_status="ready",
        recommendation="tap_verify",
        blocking_overlay=True,
        confidence=0.9,
    )
    assert _probe_indicates_ready_skip(probe) is False


def test_ready_skip_result_ok_without_panel() -> None:
    probe = ServerConnectivityProbe(
        server_slot_status="ready",
        recommendation="tap_verify",
        reason="伐毛洗髓3服",
    )
    result = _ready_skip_result("", probe)
    assert result.ok is True
    assert result.panel_opened is False
    assert "probe_ready_skip" in result.message


def test_seed_executor_flags_disables_server_check() -> None:
    state = empty_launch_graph_state()
    cfg = AppConfig.model_validate(
        {
            "llm": {"base_url": "http://x", "api_key": "k", "model_name": "m"},
            "llm_multimodal": {"base_url": "http://x", "api_key": "k", "model_name": "v"},
            "game": {"package_name": "com.test.game"},
            "executor": {"server_selector_check_enabled": False},
        }
    )
    seed_launch_graph_executor_flags(state, cfg)
    assert state["server_selector_check_enabled"] is False
    assert state["server_checked"] is True
    assert "server.check" in state.get("completed_nodes", {})
