from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from game_agent.services import deploy_runner
from game_agent.services.shutdown import ShutdownRequested, reset_shutdown_context
from game_agent.services.subprocess_tree import PopenResult


@pytest.fixture(autouse=True)
def _reset_shutdown() -> None:
    reset_shutdown_context()
    yield
    reset_shutdown_context()


def test_run_deploy_raises_on_shutdown_and_writes_log(tmp_path: Path) -> None:
    script = tmp_path / "deploy.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    log_path = tmp_path / "deploy.log"

    with (
        patch.object(deploy_runner, "DEPLOY_SCRIPT", script),
        patch.object(deploy_runner, "ANDROID_DIR", tmp_path),
        patch.object(deploy_runner, "_find_bash", return_value="bash"),
        patch.object(deploy_runner, "create_install_monitor") as monitor_factory,
        patch.object(deploy_runner, "deploy_build_locked") as build_lock,
        patch.object(deploy_runner, "popen_communicate_poll") as popen_mock,
        patch.object(deploy_runner, "is_shutdown_requested", return_value=False),
    ):
        monitor = MagicMock()
        monitor.result.summary.return_value = "ok"
        monitor_factory.return_value = monitor
        build_lock.return_value.__enter__ = MagicMock(return_value=None)
        build_lock.return_value.__exit__ = MagicMock(return_value=False)
        popen_mock.return_value = PopenResult(
            returncode=-2,
            stdout=b"partial",
            stderr=b"err",
            shutdown=True,
        )
        (tmp_path / ".gameturbo_merged_7734.json").write_text("{}", encoding="utf-8")

        with pytest.raises(ShutdownRequested):
            deploy_runner.run_deploy(
                "7734",
                serial="DEVICE1",
                artifact_root=tmp_path,
                log_filename="deploy.log",
            )

    assert log_path.is_file()
    text = log_path.read_text(encoding="utf-8")
    assert "shutdown" in text.lower()
    assert "partial" in text
