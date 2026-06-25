"""重构后关键路径与旧行为等价性检查。"""

from __future__ import annotations

from pathlib import Path

from game_agent.external_services.gameturbo.deliverables import (
    DEFAULT_OUTPUT_NAME,
    GAMETURBO_LOG_NAME,
    failure_deliverable_files,
)
from game_agent.external_services.gameturbo.config.apply import apply_gameturbo_config_patch
from game_agent.external_services.gameturbo.models.config import GameTurboConfigPatch
from game_agent.external_services.manager import (
    CORE_ANALYSIS_LOG_FILES,
    CORE_EXECUTION_LOG_FILES,
    GAMETURBO_ANALYSIS_LOG_FILES,
    GAMETURBO_EXECUTION_LOG_FILES,
    ExternalServiceManager,
)
from game_agent.models.settings import (
    AppConfig,
    ExternalServicesSection,
    GameSection,
    GameTurboPluginSection,
    GameTurboSection,
    LLMSection,
    ModulesSection,
)
from game_agent.services.failure_report import (
    ATTEMPT_FAILURE_REPORT_JSON,
    ATTEMPT_FAILURE_REPORT_MD,
)
from game_agent.external_services.gameturbo.config.apply import ConfigApplyResult


def _gt_enabled_cfg() -> AppConfig:
    return AppConfig(
        llm=LLMSection(base_url="http://x", api_key="k", model_name="gpt-4o"),
        game=GameSection(),
        gameturbo=GameTurboSection(),
        external_services=ExternalServicesSection(
            gameturbo=GameTurboPluginSection(enabled=True),
        ),
        modules=ModulesSection(executor=False),
    )


def _gt_disabled_cfg() -> AppConfig:
    return AppConfig(
        llm=LLMSection(base_url="http://x", api_key="k", model_name="gpt-4o"),
        game=GameSection(),
        gameturbo=GameTurboSection(),
        external_services=ExternalServicesSection(
            gameturbo=GameTurboPluginSection(enabled=False),
        ),
        modules=ModulesSection(executor=False),
    )


_LEGACY_FAILURE_FILES = (
    GAMETURBO_LOG_NAME,
    DEFAULT_OUTPUT_NAME,
    "ai_analysis_report.txt",
    ATTEMPT_FAILURE_REPORT_MD,
    ATTEMPT_FAILURE_REPORT_JSON,
    "process.log",
    "deploy.log",
    "pipeline_trace.jsonl",
)


def test_failure_deliverable_files_match_legacy_when_plugin_enabled() -> None:
    files = failure_deliverable_files(gameturbo_enabled=True)
    assert set(files) == set(_LEGACY_FAILURE_FILES)


def test_failure_deliverable_files_omit_plugin_artifacts_when_disabled() -> None:
    files = failure_deliverable_files(gameturbo_enabled=False)
    assert GAMETURBO_LOG_NAME not in files
    assert DEFAULT_OUTPUT_NAME not in files
    assert "process.log" in files


def test_execution_archive_plan_matches_legacy_when_plugin_enabled() -> None:
    mgr = ExternalServiceManager(_gt_enabled_cfg())
    plan = mgr.execution_log_archive_plan()
    assert plan.execution_files == CORE_EXECUTION_LOG_FILES + GAMETURBO_EXECUTION_LOG_FILES
    assert plan.analysis_files == CORE_ANALYSIS_LOG_FILES + GAMETURBO_ANALYSIS_LOG_FILES
    assert plan.session_log_glob == "gameturbo_session_*.log"
    assert plan.prepare_artifact is not None


def test_execution_archive_plan_omits_plugin_when_disabled() -> None:
    mgr = ExternalServiceManager(_gt_disabled_cfg())
    plan = mgr.execution_log_archive_plan()
    assert GAMETURBO_LOG_NAME not in plan.execution_files
    assert DEFAULT_OUTPUT_NAME not in plan.analysis_files
    assert plan.session_log_glob is None
    assert plan.prepare_artifact is None


def test_utils_shims_reexport_same_apply_types() -> None:
    assert ConfigApplyResult is not None
    patch = GameTurboConfigPatch(direct_patterns=["cdn.example.com"])
    cfg_path = Path(__file__).parent / "_parity_config.json"
    cfg_path.write_text('{"direct_patterns": []}\n', encoding="utf-8")
    try:
        result = apply_gameturbo_config_patch(cfg_path, patch)
        assert result.changed
        assert "direct_patterns" in "".join(result.summary)
    finally:
        cfg_path.unlink(missing_ok=True)


def test_manager_and_orchestrator_use_same_plugin_flag() -> None:
    cfg = _gt_enabled_cfg()
    mgr = ExternalServiceManager(cfg)
    assert mgr.gameturbo_enabled() is bool(cfg.external_services.gameturbo.enabled)
