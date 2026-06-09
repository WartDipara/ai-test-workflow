from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from game_agent.controllers.orchestrator import GameTestOrchestrator
from game_agent.models.run_failure import ErrorCode, RunFailure
from game_agent.services.shutdown import ShutdownRequested, reset_shutdown_context


@pytest.fixture(autouse=True)
def _reset_shutdown() -> None:
    reset_shutdown_context()
    yield
    reset_shutdown_context()


def test_on_attempt_failure_skips_retry_on_shutdown(tmp_path: Path) -> None:
    from game_agent.services.shutdown import get_shutdown_context

    get_shutdown_context().request_shutdown("SIGINT")
    orch = GameTestOrchestrator.__new__(GameTestOrchestrator)
    orch._last_failure_reason = ""
    orch._artifact_root = tmp_path
    orch._audit = None
    orch._task_journal = None
    orch._deliverable = None
    orch._adb = MagicMock()
    orch._app_config = MagicMock()
    orch._config_path = tmp_path / "settings.yaml"
    orch._last_blocked_stage_hint = ""

    mods = MagicMock()
    mods.retry_on_failure = True

    with patch.object(orch, "_handle_failure_sync") as handle_mock:
        with pytest.raises(ShutdownRequested):
            orch._on_attempt_failure(
                retry=1,
                max_retries=3,
                mods=mods,
                reason="Log anomaly detected: tunnel closed",
            )

    handle_mock.assert_called_once()
    assert handle_mock.call_args.kwargs["will_retry"] is False


def test_retry_handler_skips_modify_when_shutdown() -> None:
    from game_agent.controllers.retry_controller import AnomalyHandler
    from game_agent.services.shutdown import get_shutdown_context

    get_shutdown_context().request_shutdown("SIGINT")
    handler = AnomalyHandler(
        adb=MagicMock(),
        app_config=MagicMock(),
        config_path=Path("settings.yaml"),
        artifact_root=None,
    )
    failure = RunFailure(ErrorCode.NET_LOG_ANOMALY, "log anomaly", retryable=True)

    with (
        patch(
            "game_agent.controllers.retry_controller.FailureCleanup",
        ) as cleanup_cls,
        patch(
            "game_agent.controllers.retry_controller.RetryConfigHandler",
        ) as retry_cls,
        patch(
            "game_agent.controllers.retry_controller.generate_and_save_attempt_failure_report",
            new_callable=AsyncMock,
        ),
    ):
        cleanup_cls.return_value.run = AsyncMock()
        import asyncio

        asyncio.run(
            handler.handle(1, failure, run_retry_config=True, will_retry=True),
        )

    retry_cls.assert_not_called()
