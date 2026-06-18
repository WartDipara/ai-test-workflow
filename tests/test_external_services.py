from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from game_agent.config.loader import load_app_config
from game_agent.core.app_installer import CoreAppInstaller
from game_agent.core.apk_identity import build_core_prepared_app
from game_agent.external_services.manager import ExternalServiceManager
from game_agent.models.settings import ExternalServicesSection, GameTurboPluginSection
from game_agent.models.task_config import TaskConfig
from game_agent.models.task_runtime import TaskRuntime
from game_agent.services.run_deliverable import (
    RunDeliverablePaths,
    publish_core_success_deliverable,
)


_MINIMAL_YAML = """
llm:
  base_url: "http://x"
  api_key: "k"
  model_name: "gpt-4o"
game:
  timeout_s: 100
external_services:
  gameturbo:
    enabled: false
modules:
  executor: false
"""


def test_external_services_defaults_disabled(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(_MINIMAL_YAML, encoding="utf-8")
    cfg = load_app_config(path)
    assert cfg.external_services.gameturbo.enabled is False


def test_legacy_gameturbo_enabled_migrates(tmp_path: Path) -> None:
    yaml = """
llm:
  base_url: "http://x"
  api_key: "k"
  model_name: "gpt-4o"
game:
  timeout_s: 100
gameturbo:
  enabled: true
  run_outputs_dir: ./out
modules:
  executor: false
"""
    path = tmp_path / "settings.yaml"
    path.write_text(yaml, encoding="utf-8")
    cfg = load_app_config(path)
    assert cfg.external_services.gameturbo.enabled is True


def test_manager_log_monitor_off_when_plugin_disabled(tmp_path: Path) -> None:
    yaml = _MINIMAL_YAML + """
modules:
  executor: false
  log_monitor: true
"""
    path = tmp_path / "settings.yaml"
    path.write_text(yaml, encoding="utf-8")
    cfg = load_app_config(path)
    runtime = TaskRuntime(
        task_id="t1",
        index=0,
        serial="dev",
        apk_url="",
        batch_root=tmp_path,
        task_cache_dir=tmp_path / "cache",
        package_name="com.game",
        launch_activity="com.game/.Main",
    )
    task_cfg = TaskConfig(cfg, runtime)
    manager = ExternalServiceManager(cfg)
    from game_agent.external_services.context import ServiceContext

    ctx = ServiceContext(
        config_path=path,
        app_config=task_cfg,
        adb=MagicMock(),
        artifact_root=tmp_path / "art",
        deliverable_root=None,
        retry=1,
        max_retries=1,
    )
    assert manager.effective_log_monitor(ctx) is False
    assert manager.effective_retry_config(ctx) is False


def test_core_installer_skips_when_installed(tmp_path: Path) -> None:
    apk = tmp_path / "game.apk"
    apk.write_bytes(b"fake")
    adb = MagicMock()
    adb.is_package_installed.return_value = True
    prepared = build_core_prepared_app(apk, skip_install=False)
    prepared.package_name = "com.game"
    result = CoreAppInstaller(adb).install_if_needed(prepared)
    assert result.ok
    assert result.skipped
    adb.install_apk.assert_not_called()


def test_core_installer_calls_adb_install(tmp_path: Path) -> None:
    apk = tmp_path / "game.apk"
    apk.write_bytes(b"fake")
    adb = MagicMock()
    adb.is_package_installed.side_effect = [False, True]
    with patch(
        "game_agent.core.app_installer.install_apk_with_monitor",
        return_value=("Installed: game.apk", "polls=3 clicks=1"),
    ) as install_mock:
        prepared = build_core_prepared_app(apk)
        prepared.package_name = "com.game"
        art = tmp_path / "artifact"
        art.mkdir()
        result = CoreAppInstaller(adb, artifact_root=art).install_if_needed(prepared)
        assert result.ok
        assert result.install_monitor_summary == "polls=3 clicks=1"
        install_mock.assert_called_once_with(
            adb,
            apk,
            timeout_s=300.0,
            artifact_root=art,
        )


def test_publish_core_success_deliverable(tmp_path: Path) -> None:
    deliverable = RunDeliverablePaths(
        task_id="tid",
        gid="12345",
        root=tmp_path / "out",
    )
    deliverable.root.mkdir()
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    apk = tmp_path / "game.apk"
    apk.write_bytes(b"x")
    publish_core_success_deliverable(
        deliverable,
        winning_artifact_root=artifact,
        winning_retry=1,
        total_attempts=1,
        package_name="com.game",
        source_apk=apk,
        install_apk=apk,
        in_game_confirmed=True,
    )
    import json

    data = json.loads((deliverable.root / "result.json").read_text(encoding="utf-8"))
    assert data["success"] is True
    assert data["in_game_confirmed"] is True
    assert data["package_name"] == "com.game"
    assert "merged_config" not in data
