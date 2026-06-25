from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from game_agent.exceptions import (
    ConfigPatchGenerationError,
    ConfigPatchLlmError,
    ConfigPatchRejectedError,
)
from game_agent.external_services.gameturbo.models.config import GameTurboConfigPatch
from game_agent.models.run_failure import ErrorCode, classify_failure
from game_agent.external_services.gameturbo.retry.analysis import AnalysisAgent
from game_agent.external_services.gameturbo.retry.modify import (
    RetryConfigHandler,
    _patch_has_actionable_changes,
)
from game_agent.external_services.gameturbo.config.apply import ConfigApplyResult


def test_classify_config_patch_llm_error_non_retryable() -> None:
    exc = ConfigPatchLlmError("AI 配置补丁请求失败: 500", attempt=3, max_attempts=3)
    failure = classify_failure(str(exc), exc=exc)
    assert failure.code == ErrorCode.LLM_API
    assert failure.retryable is False
    assert "Modify 阶段 AI 请求失败" in failure.message
    assert "attempts=3/3" in failure.detail


def test_classify_config_patch_rejected_non_retryable() -> None:
    exc = ConfigPatchRejectedError(
        "AI 分析认为当前日志/域名下无可安全追加的配置变更",
        analysis="no CDN candidates",
    )
    failure = classify_failure(str(exc), exc=exc)
    assert failure.code == ErrorCode.CONFIG
    assert failure.retryable is False
    assert "无可修改配置" in failure.message
    assert "no CDN candidates" in failure.detail


def test_patch_has_actionable_changes() -> None:
    assert not _patch_has_actionable_changes(GameTurboConfigPatch(analysis="x"))
    assert _patch_has_actionable_changes(
        GameTurboConfigPatch(analysis="x", direct_patterns=["cdn.example.com"]),
    )


def test_analysis_agent_raises_without_domain_json() -> None:
    agent = AnalysisAgent.__new__(AnalysisAgent)

    async def _run() -> None:
        with pytest.raises(ConfigPatchGenerationError) as exc_info:
            await AnalysisAgent.analyze_and_propose_patch(
                agent,
                anomaly_reason="net fail",
                log_content="",
                current_config={},
                domain_analysis=None,
            )
        assert exc_info.value.stage == "domain_analysis"

    asyncio.run(_run())


def test_analysis_agent_raises_on_llm_error() -> None:
    agent = AnalysisAgent.__new__(AnalysisAgent)
    agent._patch_agent = MagicMock()
    agent._patch_agent.run = AsyncMock(side_effect=RuntimeError("status_code: 500"))

    async def _run() -> None:
        with pytest.raises(ConfigPatchLlmError) as exc_info:
            await AnalysisAgent.analyze_and_propose_patch(
                agent,
                anomaly_reason="net fail",
                log_content="log",
                current_config={"game_id": "1"},
                domain_analysis={"tunnel_domains": []},
            )
        assert exc_info.value.stage == "llm_patch"

    asyncio.run(_run())


def test_retry_config_fails_without_domain_json(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    handler = RetryConfigHandler(
        adb=MagicMock(),
        app_config=MagicMock(),
        config_path=tmp_path / "settings.yaml",
        artifact_root=artifact,
    )
    with pytest.raises(ConfigPatchGenerationError) as exc_info:
        handler._require_domain_analysis_json(artifact / "gameturbo.log")
    assert exc_info.value.stage == "domain_analysis"


def test_retry_config_fails_on_empty_patch(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    domain_path = artifact / "domain_region_analysis.json"
    domain_path.write_text(
        json.dumps({"tunnel_domains": [], "direct_domains": []}),
        encoding="utf-8",
    )
    (artifact / "gameturbo.log").write_text("GameTurbo log", encoding="utf-8")
    game_cfg = tmp_path / "gameturbo_1_test.json"
    game_cfg.write_text(
        json.dumps(
            {
                "game_id": "1",
                "default_action": "tunnel",
                "direct_patterns": [],
                "port_rules": [],
            },
        ),
        encoding="utf-8",
    )
    deliverable = tmp_path / "out"
    deliverable.mkdir()
    handler = RetryConfigHandler(
        adb=MagicMock(),
        app_config=MagicMock(runtime=MagicMock(gid="1", game_config_path=game_cfg)),
        config_path=tmp_path / "settings.yaml",
        artifact_root=artifact,
        task_deliverable_root=deliverable,
    )
    handler.app_config.runtime.require_gameturbo = MagicMock()
    handler.app_config.gameturbo = MagicMock(modify_patch_max_llm_retries=3)
    empty_patch = GameTurboConfigPatch(analysis="no changes proposed")

    async def _run() -> None:
        with (
            patch.object(
                RetryConfigHandler,
                "_invoke_ai_patch_once",
                new=AsyncMock(return_value=empty_patch),
            ),
            patch("game_agent.external_services.gameturbo.retry.modify.prepare_modify_stage") as prep,
            patch("game_agent.external_services.gameturbo.retry.modify.run_deploy_with_ai_retry", new=AsyncMock()),
        ):
            prep.return_value = (deliverable / "before.json", None)
            with pytest.raises(ConfigPatchRejectedError) as exc_info:
                await handler.run(1, "Network anomaly confirmed")
            assert exc_info.value.stage == "ai_rejected"

    asyncio.run(_run())


def test_retry_config_fails_when_apply_unchanged(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    domain_path = artifact / "domain_region_analysis.json"
    domain_path.write_text(json.dumps({"tunnel_domains": []}), encoding="utf-8")
    (artifact / "gameturbo.log").write_text("log", encoding="utf-8")
    game_cfg = tmp_path / "gameturbo_1_test.json"
    game_cfg.write_text(
        json.dumps(
            {
                "game_id": "1",
                "default_action": "tunnel",
                "direct_patterns": ["cdn.example.com"],
                "port_rules": [],
            },
        ),
        encoding="utf-8",
    )
    deliverable = tmp_path / "out"
    deliverable.mkdir()
    handler = RetryConfigHandler(
        adb=MagicMock(),
        app_config=MagicMock(runtime=MagicMock(gid="1", game_config_path=game_cfg)),
        config_path=tmp_path / "settings.yaml",
        artifact_root=artifact,
        task_deliverable_root=deliverable,
    )
    handler.app_config.runtime.require_gameturbo = MagicMock()
    handler.app_config.gameturbo = MagicMock(modify_patch_max_llm_retries=3)
    config_patch = GameTurboConfigPatch(
        analysis="duplicate",
        direct_patterns=["cdn.example.com"],
    )

    async def _run() -> None:
        with (
            patch.object(
                RetryConfigHandler,
                "_invoke_ai_patch_once",
                new=AsyncMock(return_value=config_patch),
            ),
            patch.object(
                RetryConfigHandler,
                "_require_domain_analysis_json",
                return_value={"tunnel_domains": []},
            ),
            patch(
                "game_agent.external_services.gameturbo.retry.modify.apply_gameturbo_config_patch",
                return_value=ConfigApplyResult(path=game_cfg, changed=False, summary=[]),
            ),
            patch("game_agent.external_services.gameturbo.retry.modify.prepare_modify_stage") as prep,
            patch("game_agent.external_services.gameturbo.retry.modify.run_deploy_with_ai_retry", new=AsyncMock()),
        ):
            prep.return_value = (deliverable / "before.json", None)
            with pytest.raises(ConfigPatchGenerationError) as exc_info:
                await handler.run(1, "Network anomaly confirmed")
            assert exc_info.value.stage == "noop_patch"

    asyncio.run(_run())


def test_retry_config_llm_retries_then_succeeds(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    game_cfg = tmp_path / "gameturbo_1_test.json"
    game_cfg.write_text(
        json.dumps(
            {
                "game_id": "1",
                "default_action": "tunnel",
                "direct_patterns": [],
                "port_rules": [],
            },
        ),
        encoding="utf-8",
    )
    handler = RetryConfigHandler(
        adb=MagicMock(),
        app_config=MagicMock(runtime=MagicMock(gid="1", game_config_path=game_cfg)),
        config_path=tmp_path / "settings.yaml",
        artifact_root=artifact,
    )
    handler.app_config.runtime.require_gameturbo = MagicMock()
    handler.app_config.gameturbo = MagicMock(modify_patch_max_llm_retries=3)
    ok_patch = GameTurboConfigPatch(
        analysis="add cdn",
        direct_patterns=["cdn.example.com"],
    )
    invoke = AsyncMock(
        side_effect=[
            ConfigPatchLlmError("500"),
            ConfigPatchLlmError("timeout"),
            ok_patch,
        ],
    )

    async def _run() -> None:
        with patch.object(RetryConfigHandler, "_invoke_ai_patch_once", new=invoke):
            result = await handler._run_ai_patch(
                reason="net fail",
                local_log_path=artifact / "gameturbo.log",
                domain_analysis={"tunnel_domains": []},
                game_config_path=game_cfg,
                failed_attempt=1,
            )
        assert result.direct_patterns == ["cdn.example.com"]
        assert invoke.await_count == 3

    asyncio.run(_run())


def test_retry_config_llm_exhausted_raises(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    game_cfg = tmp_path / "gameturbo_1_test.json"
    game_cfg.write_text("{}", encoding="utf-8")
    handler = RetryConfigHandler(
        adb=MagicMock(),
        app_config=MagicMock(runtime=MagicMock(gid="1", game_config_path=game_cfg)),
        config_path=tmp_path / "settings.yaml",
        artifact_root=artifact,
    )
    handler.app_config.gameturbo = MagicMock(modify_patch_max_llm_retries=2)
    invoke = AsyncMock(side_effect=ConfigPatchLlmError("500"))

    async def _run() -> None:
        with (
            patch.object(RetryConfigHandler, "_invoke_ai_patch_once", new=invoke),
            pytest.raises(ConfigPatchLlmError) as exc_info,
        ):
            await handler._run_ai_patch(
                reason="net fail",
                local_log_path=artifact / "gameturbo.log",
                domain_analysis={"tunnel_domains": []},
                game_config_path=game_cfg,
                failed_attempt=1,
            )
        assert exc_info.value.attempt == 2
        assert exc_info.value.max_attempts == 2
        assert "已重试 2 次" in str(exc_info.value)
        assert invoke.await_count == 2

    asyncio.run(_run())


def test_retry_config_deploys_after_valid_patch(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    game_cfg = tmp_path / "gameturbo_1_test.json"
    game_cfg.write_text(
        json.dumps(
            {
                "game_id": "1",
                "default_action": "tunnel",
                "direct_patterns": [],
                "port_rules": [],
            },
        ),
        encoding="utf-8",
    )
    deliverable = tmp_path / "out"
    deliverable.mkdir()
    handler = RetryConfigHandler(
        adb=MagicMock(),
        app_config=MagicMock(runtime=MagicMock(gid="1", game_config_path=game_cfg)),
        config_path=tmp_path / "settings.yaml",
        artifact_root=artifact,
        task_deliverable_root=deliverable,
    )
    handler.app_config.runtime.require_gameturbo = MagicMock()
    handler.app_config.gameturbo = MagicMock(modify_patch_max_llm_retries=3)
    config_patch = GameTurboConfigPatch(
        analysis="add cdn",
        direct_patterns=["cdn.example.com"],
    )
    deploy_mock = AsyncMock(return_value=MagicMock(returncode=0, log_path=None))

    async def _run() -> None:
        with (
            patch.object(
                RetryConfigHandler,
                "_invoke_ai_patch_once",
                new=AsyncMock(return_value=config_patch),
            ),
            patch.object(
                RetryConfigHandler,
                "_require_domain_analysis_json",
                return_value={"tunnel_domains": []},
            ),
            patch(
                "game_agent.external_services.gameturbo.retry.modify.apply_gameturbo_config_patch",
                return_value=ConfigApplyResult(
                    path=game_cfg,
                    changed=True,
                    summary=["direct_patterns: added 1 pattern(s)"],
                ),
            ),
            patch("game_agent.external_services.gameturbo.retry.modify.prepare_modify_stage") as prep,
            patch("game_agent.external_services.gameturbo.retry.modify.record_patch_applied"),
            patch("game_agent.external_services.gameturbo.retry.modify.run_deploy_with_ai_retry", deploy_mock),
        ):
            prep.return_value = (deliverable / "before.json", None)
            await handler.run(1, "Network anomaly confirmed")
        deploy_mock.assert_awaited_once()

    asyncio.run(_run())
