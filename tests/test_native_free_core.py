from __future__ import annotations

import ast
from pathlib import Path

import pytest
import zipfile

from game_agent.config.loader import load_app_config
from game_agent.core.apk_staging import parse_gid_from_apk_name, resolve_task_gid
from game_agent.core.deliverables import resolve_deliverables_dir
from game_agent.core.external_log import resolve_external_log_reader
from game_agent.models.settings import (
    AppConfig,
    ExternalServicesSection,
    GameSection,
    GameTurboPluginSection,
    GameTurboSection,
    LLMSection,
    ModulesSection,
)
from game_agent.modules.preprocessing.preprocessor import Preprocessor


_REPO_ROOT = Path(__file__).resolve().parent.parent
_CORE_SCAN_ROOTS = (
    _REPO_ROOT / "game_agent" / "controllers",
    _REPO_ROOT / "game_agent" / "graphs",
    _REPO_ROOT / "game_agent" / "models",
    _REPO_ROOT / "game_agent" / "modules",
    _REPO_ROOT / "game_agent" / "core",
    _REPO_ROOT / "game_agent" / "paths.py",
)

_FORBIDDEN_IMPORT_MODULES = (
    "game_agent.utils.gameturbo_bootstrap",
    "game_agent.services.gameturbo_log",
)

_LEGACY_GT_ADJACENT = {
    "game_agent/controllers/log_monitor_controller.py",
    "game_agent/controllers/session_controller.py",
    "game_agent/controllers/executor_controller.py",
    "game_agent/modules/retry/analysis.py",
    "game_agent/modules/retry/deploy_retry.py",
    "game_agent/modules/retry/retry_config.py",
    "game_agent/core/external_log.py",
}


def _rel_posix(path: Path) -> str:
    return str(path.relative_to(_REPO_ROOT)).replace("\\", "/")


def _iter_core_python_files() -> list[Path]:
    files: list[Path] = []
    for root in _CORE_SCAN_ROOTS:
        if root.is_file():
            files.append(root)
            continue
        files.extend(root.rglob("*.py"))
    excluded = (
        "external_services/gameturbo",
        "utils/gameturbo_bootstrap.py",
    )
    return [
        path
        for path in files
        if not any(part in _rel_posix(path) for part in excluded)
    ]


def _forbidden_top_level_imports(path: Path) -> list[str]:
    rel = _rel_posix(path)
    if rel in _LEGACY_GT_ADJACENT:
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if any(mod == prefix or mod.startswith(prefix + ".") for prefix in _FORBIDDEN_IMPORT_MODULES):
                offenders.append(mod)
    return offenders


def test_core_modules_avoid_gameturbo_imports() -> None:
    offenders: list[str] = []
    for path in _iter_core_python_files():
        rel = _rel_posix(path)
        for mod in _forbidden_top_level_imports(path):
            offenders.append(f"{rel}: import from {mod}")
    assert not offenders, "核心层顶层仍 import GameTurbo:\n" + "\n".join(offenders)


def test_core_imports_succeed() -> None:
    import game_agent.controllers.orchestrator  # noqa: F401
    import game_agent.graphs.launch_flow  # noqa: F401
    import game_agent.modules.retry.cleanup  # noqa: F401
    from game_agent.core.apk_staging import resolve_task_gid as _resolve  # noqa: F401


def test_plugin_disabled_preprocessor_does_not_mkdir_packages(tmp_path: Path) -> None:
    cache = tmp_path / "apk_cache"
    cache.mkdir()
    apk = cache / "12345_game.apk"
    with zipfile.ZipFile(apk, "w") as zf:
        zf.writestr("AndroidManifest.xml", "<manifest/>")
    packages = tmp_path / "GameTurbo-Native" / "client" / "android" / "packages"
    pre = Preprocessor(cache_dir=cache, packages_dir=None)
    result = pre.run()
    assert result.ok
    assert not packages.exists()


def test_resolve_deliverables_dir_uses_run_outputs() -> None:
    cfg = AppConfig(
        llm=LLMSection(base_url="http://x", api_key="k", model_name="gpt-4o"),
        game=GameSection(),
        gameturbo=GameTurboSection(run_outputs_dir=Path("./run_outputs")),
        modules=ModulesSection(executor=False),
    )
    assert resolve_deliverables_dir(cfg) == Path("./run_outputs")


def test_external_log_reader_none_when_plugin_disabled() -> None:
    cfg = AppConfig(
        llm=LLMSection(base_url="http://x", api_key="k", model_name="gpt-4o"),
        game=GameSection(),
        gameturbo=GameTurboSection(),
        external_services=ExternalServicesSection(
            gameturbo=GameTurboPluginSection(enabled=False),
        ),
        modules=ModulesSection(executor=False),
    )
    assert resolve_external_log_reader(cfg) is None


def test_resolve_task_gid_from_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    apk = cache / "99999_demo.apk"
    apk.write_bytes(b"x")
    assert resolve_task_gid("", cache_dir=cache) == "99999"
    assert parse_gid_from_apk_name(apk) == "99999"


def test_settings_run_outputs_dir_loads(tmp_path: Path) -> None:
    yaml = """
llm:
  base_url: "http://x"
  api_key: "k"
  model_name: "gpt-4o"
game: {}
gameturbo:
  run_outputs_dir: "./custom_out"
modules:
  executor: false
"""
    path = tmp_path / "settings.yaml"
    path.write_text(yaml, encoding="utf-8")
    cfg = load_app_config(path)
    assert cfg.gameturbo.run_outputs_dir == Path("./custom_out")
    assert resolve_deliverables_dir(cfg) == Path("./custom_out")
