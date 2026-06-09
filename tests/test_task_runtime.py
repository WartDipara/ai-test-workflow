from __future__ import annotations

from pathlib import Path

from game_agent.models.settings import (
    AppConfig,
    GameSection,
    GameTurboSection,
    LLMSection,
    ModulesSection,
)
from game_agent.models.task_config import TaskConfig
from game_agent.models.task_runtime import TaskRuntime, TaskRuntimeRegistry


def _minimal_config() -> AppConfig:
    return AppConfig(
        llm=LLMSection(base_url="http://x", api_key="k", model_name="gpt-4o"),
        game=GameSection(),
        gameturbo=GameTurboSection(),
        modules=ModulesSection(executor=False),
    )


def test_task_config_merges_runtime(tmp_path: Path) -> None:
    runtime = TaskRuntime(
        task_id="t1",
        index=0,
        serial="dev1",
        apk_url="http://example/a.apk",
        batch_root=tmp_path,
        task_cache_dir=tmp_path / "cache",
        gid="15993",
        package_name="com.game",
        launch_activity="com.game/.Main",
        source_apk=tmp_path / "15993_game.apk",
        game_config_path=tmp_path / "gameturbo_15993_test.json",
    )
    cfg = TaskConfig(_minimal_config(), runtime)
    assert cfg.game.package_name == "com.game"
    assert cfg.game.launch_activity == "com.game/.Main"
    assert cfg.gameturbo.gid == "15993"
    assert cfg.game.timeout_s == 300.0


def test_task_runtime_registry_lookup() -> None:
    TaskRuntimeRegistry.clear()
    rt = TaskRuntime(
        task_id="abc",
        index=0,
        serial="s",
        apk_url="u",
        batch_root=Path("/tmp"),
        task_cache_dir=Path("/tmp/c"),
        gid="42",
    )
    TaskRuntimeRegistry.register(rt)
    assert TaskRuntimeRegistry.get("abc") is rt
    assert TaskRuntimeRegistry.get_by_gid("42") is rt
    TaskRuntimeRegistry.clear()
    assert TaskRuntimeRegistry.get("abc") is None
