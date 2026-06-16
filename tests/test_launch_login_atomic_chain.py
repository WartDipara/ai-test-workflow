from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from game_agent.graphs.launch_flow import build_launch_graph
from game_agent.graphs.launch_routing import route_next
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.services.login_batch_fill import AtomicLoginResult


def _state(**kwargs):
    state = empty_launch_graph_state()
    facts = kwargs.pop("facts", LaunchFacts())
    state["facts"] = facts.model_dump()
    state.update(kwargs)
    return state


def test_route_login_uses_atomic_login() -> None:
    state = _state(
        facts=LaunchFacts(login_blocking=True, login_stage="login_form"),
        login_done=False,
    )
    assert route_next(state) == "atomic_login"


def test_atomic_login_graph_edge_skips_observe_on_success() -> None:
    deps = MagicMock()
    deps.app_config = MagicMock()
    deps.adb = MagicMock()
    deps.run_state = MagicMock()
    deps.artifact_root = Path("/tmp/graph_atomic")
    deps.settings_path = Path("/tmp/settings.yaml")
    deps.screen_width = 1080
    deps.screen_height = 2400
    deps.round_id = 0
    deps.attempt_context = None
    deps.audit = None
    deps.vision_queue = None

    graph = build_launch_graph(deps)
    compiled = graph.get_graph()
    login_edges = [e for e in compiled.edges if e.source == "atomic_login"]
    targets = {e.target for e in login_edges}
    assert targets == {"observe_screen", "classify_screen"}
    assert all(e.conditional for e in login_edges)


def test_atomic_login_node_sets_all_flags(tmp_path: Path) -> None:
    from game_agent.graphs.launch_deps import LaunchGraphDeps
    from game_agent.graphs.launch_nodes import atomic_login_node
    from game_agent.models.run_state import RunState

    cred = MagicMock()
    cred.username = "user@example.com"
    cred.password = "secret"

    deps = LaunchGraphDeps(
        app_config=MagicMock(),
        adb=MagicMock(),
        run_state=RunState(),
        artifact_root=tmp_path,
        settings_path=Path("/tmp/settings.yaml"),
    )
    deps.screen_width = 1080
    deps.screen_height = 2400
    deps.app_config.executor = MagicMock()
    deps.app_config.credentials.file_path = "creds.yaml"

    ok_result = AtomicLoginResult(
        ok=True,
        message="filled and verified",
        stage="clear",
        left_login_form=True,
    )

    async def _run() -> None:
        with (
            patch(
                "game_agent.graphs.launch_nodes.load_game_credentials",
                return_value=cred,
            ),
            patch(
                "game_agent.graphs.launch_nodes.atomic_login_fill_and_submit",
                return_value=ok_result,
            ),
        ):
            out = await atomic_login_node(_state(), deps)

        assert out["account_filled"] is True
        assert out["password_filled"] is True
        assert out["login_submitted"] is True
        assert out["login_done"] is True

    asyncio.run(_run())


def test_recover_login_skips_vision_and_retries_atomic(tmp_path: Path) -> None:
    from game_agent.graphs.launch_deps import LaunchGraphDeps
    from game_agent.graphs.launch_nodes import recover_from_failure_node
    from game_agent.models.run_state import RunState

    adb = MagicMock()
    adb.touch_size.return_value = (1080, 2400)
    adb.device_serial = "dev1"
    adb.screencap_png = MagicMock()

    deps = LaunchGraphDeps(
        app_config=MagicMock(),
        adb=adb,
        run_state=RunState(),
        artifact_root=tmp_path,
        settings_path=Path("/tmp/settings.yaml"),
    )
    deps.screen_width = 1080
    deps.screen_height = 2400
    deps.app_config.executor = MagicMock()
    deps.app_config.credentials.file_path = "creds.yaml"
    deps.app_config.llm_multimodal = MagicMock()

    cred = MagicMock()
    cred.username = "user@example.com"
    cred.password = "secret"

    submit_ok = AtomicLoginResult(
        ok=True,
        message="atomic ok",
        stage="clear",
        left_login_form=True,
    )

    async def _run_recover() -> None:
        with (
            patch(
                "game_agent.graphs.launch_nodes.format_latest_gameturbo_log_for_agent",
                return_value="",
            ),
            patch(
                "game_agent.graphs.launch_nodes.load_game_credentials",
                return_value=cred,
            ),
            patch(
                "game_agent.graphs.launch_nodes.run_ocr_frame",
                return_value=("", []),
            ),
            patch(
                "game_agent.graphs.launch_nodes.is_login_secure_keyboard_blackout",
                return_value=True,
            ),
            patch(
                "game_agent.graphs.launch_nodes.try_dismiss_login_secure_keyboard",
                return_value="dismissed",
            ),
            patch(
                "game_agent.graphs.launch_nodes.atomic_login_fill_and_submit",
                return_value=submit_ok,
            ),
            patch(
                "game_agent.graphs.launch_nodes.run_analyze_screen",
                new=AsyncMock(),
            ) as mock_vision,
        ):
            state = _state(
                account_filled=True,
                password_filled=False,
                login_submitted=False,
            )
            out = await recover_from_failure_node(state, deps)

        mock_vision.assert_not_called()
        assert out.get("login_done") is True
        assert "blind retry atomic_login OK" in (out.get("recover_hint") or "")
        assert "skip analyze_screen (login blind recover)" in (out.get("recover_hint") or "")

    asyncio.run(_run_recover())
