from __future__ import annotations

from pathlib import Path

import pytest

from game_agent.config.loader import load_app_config
from game_agent.models.settings import AgentSection, ExecutorSection, GameSection, GameTurboSection
from game_agent.models.task_config import TaskConfig
from game_agent.models.task_runtime import TaskRuntime


_MINIMAL_YAML = """
llm:
  base_url: "http://x"
  api_key: "k"
  model_name: "gpt-4o"
game:
  package_name: "legacy.pkg"
  launch_activity: "legacy.pkg/.Main"
  launch_detect_timeout_s: 90.0
  launch_detect_poll_interval_s: 2.0
  timeout_s: 100
gameturbo:
  gid: "99999"
  game_config_path: "/tmp/old.json"
  source_apk: "/tmp/old.apk"
  deploy_timeout_s: 60
  run_outputs_dir: "./run_outputs"
executor:
  ad_initial_wait_s: 3.0
  max_foreground_retries: 4
detection:
  api_url: "http://legacy/predict"
agent:
  max_rounds: 100
  persist_learned_skill_on_success: true
modules:
  executor: false
"""


def test_load_strips_legacy_runtime_fields_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(_MINIMAL_YAML, encoding="utf-8")
    cfg = load_app_config(path)
    assert cfg.game.timeout_s == 100.0
    assert "package_name" not in GameSection.model_fields
    assert "launch_detect_timeout_s" not in GameSection.model_fields
    assert "gid" not in GameTurboSection.model_fields
    assert "ad_initial_wait_s" not in ExecutorSection.model_fields
    assert "max_rounds" not in AgentSection.model_fields


def test_task_lifecycle_does_not_modify_settings_file(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(_MINIMAL_YAML, encoding="utf-8")
    before = path.read_bytes()

    cfg = load_app_config(path)
    runtime = TaskRuntime(
        task_id="t1",
        index=0,
        serial="dev",
        apk_url="http://example/a.apk",
        batch_root=tmp_path / "batch",
        task_cache_dir=tmp_path / "cache",
        gid="15993",
        package_name="com.game",
        launch_activity="com.game/.Main",
    )
    TaskConfig(cfg, runtime)
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    assert path.read_bytes() == before


def test_no_settings_yaml_writer_module() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("game_agent.utils.settings_yaml")
