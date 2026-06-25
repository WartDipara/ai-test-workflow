from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from game_agent.external_services.gameturbo.deploy import runner as deploy_runner


def test_run_deploy_queue_mode_command(tmp_path: Path) -> None:
    script = tmp_path / "deploy.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    merged = Path(".gameturbo_merged_15993.json")

    with (
        patch.object(deploy_runner, "DEPLOY_SCRIPT", script),
        patch.object(deploy_runner, "ANDROID_DIR", tmp_path),
        patch.object(deploy_runner, "_find_bash", return_value="bash"),
        patch.object(deploy_runner, "InstallMonitorSession") as session_cls,
        patch.object(deploy_runner, "deploy_build_locked") as build_lock,
        patch.object(deploy_runner, "popen_communicate_poll") as popen_mock,
        patch.object(deploy_runner, "_verify_package_on_device"),
    ):
        from game_agent.services.subprocess_tree import PopenResult

        popen_result = PopenResult(returncode=0, stdout=b"", stderr=b"")
        monitor = MagicMock()
        monitor.result.errors = []
        monitor.result.polls = 0
        session = MagicMock()
        session.monitor = monitor
        session._results = []
        session.run_while.side_effect = lambda action: action()
        session_cls.start.return_value = session
        build_lock.return_value.__enter__ = MagicMock(return_value=None)
        build_lock.return_value.__exit__ = MagicMock(return_value=False)
        popen_mock.return_value = popen_result
        artifact = tmp_path / "retry_1"
        artifact.mkdir()
        merged_target = artifact / merged.name
        merged_target.write_text("{}", encoding="utf-8")

        deploy_runner.run_deploy(
            "15993",
            serial="DEVICE1",
            artifact_root=artifact,
            output_apk="15993_gameturbo.apk",
            merged_config_output=merged,
        )

    cmd = popen_mock.call_args.args[0]
    assert "-d" in cmd and "DEVICE1" in cmd
    assert "-o" in cmd and "packages/15993_gameturbo.apk" in cmd
    m_idx = cmd.index("-m")
    merged_arg = cmd[m_idx + 1]
    assert merged.name in merged_arg
    assert str(artifact.resolve()).replace("\\", "/") in merged_arg.replace("\\", "/")


def test_finalize_merged_config_moves_from_native(tmp_path, monkeypatch) -> None:
    from game_agent.external_services.gameturbo import bootstrap as gt_bootstrap

    native = tmp_path / "native" / ".gameturbo_merged_42.json"
    native.parent.mkdir(parents=True)
    native.write_text('{"game_id":"42"}', encoding="utf-8")
    target = tmp_path / "artifact" / ".gameturbo_merged_42.json"

    monkeypatch.setattr(
        gt_bootstrap,
        "gameturbo_merged_config_path",
        lambda gid: native,
    )
    final = gt_bootstrap.finalize_merged_config_after_deploy("42", target)
    assert final == target.resolve()
    assert target.is_file()
    assert not native.is_file()
