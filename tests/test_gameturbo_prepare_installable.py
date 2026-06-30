from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from game_agent.external_services.context import ServiceContext
from game_agent.external_services.gameturbo.bootstrap import GameTurboBootstrapResult
from game_agent.external_services.gameturbo.deploy.runner import DeployResult
from game_agent.external_services.gameturbo.service import GameTurboExternalService
from game_agent.models.settings import (
    AppConfig,
    ExternalServicesSection,
    GameSection,
    GameTurboPluginSection,
    GameTurboSection,
    LLMSection,
    ModulesSection,
)
from game_agent.models.task_config import TaskConfig
from game_agent.models.task_runtime import TaskRuntime
from game_agent.utils.apk_util import ApkLaunchInfo


def _minimal_apk(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("AndroidManifest.xml", "<manifest package='com.example.game'/>")


def test_prepare_installable_awaits_deploy_under_running_event_loop(
    tmp_path: Path,
) -> None:
    """编排器用 asyncio.run 调用 prepare_installable 时，deploy 不得再嵌套 asyncio.run。"""
    gid = "17020"
    source_apk = tmp_path / f"{gid}_game.apk"
    output_apk = tmp_path / f"{gid}_gameturbo.apk"
    game_config = tmp_path / f"gameturbo_{gid}_test.json"
    _minimal_apk(source_apk)
    game_config.write_text('{"game_id": "17020"}', encoding="utf-8")

    runtime = TaskRuntime(
        task_id="t1",
        index=0,
        serial="emulator-5554",
        apk_url="http://example/apk",
        batch_root=tmp_path,
        task_cache_dir=tmp_path / "cache",
    )
    app_config = AppConfig(
        llm=LLMSection(base_url="http://x", api_key="k", model_name="gpt-4o"),
        game=GameSection(),
        gameturbo=GameTurboSection(),
        external_services=ExternalServicesSection(
            gameturbo=GameTurboPluginSection(enabled=True),
        ),
        modules=ModulesSection(executor=False),
    )
    task_config = TaskConfig(app_config, runtime)
    adb = MagicMock()
    adb.is_package_installed.return_value = False

    async def _fake_deploy(*_args: object, **_kwargs: object) -> DeployResult:
        output_apk.write_bytes(b"apk")
        log_path = tmp_path / "deploy.log"
        log_path.write_text("ok", encoding="utf-8")
        return DeployResult(
            command=["bash", "deploy.sh"],
            cwd=tmp_path,
            log_path=log_path,
            returncode=0,
        )

    deploy_mock = AsyncMock(side_effect=_fake_deploy)
    bootstrap = GameTurboBootstrapResult(
        gid=gid,
        source_apk=source_apk,
        game_config_path=game_config,
        created_config=False,
    )
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    ctx = ServiceContext(
        config_path=tmp_path / "settings.yaml",
        app_config=task_config,
        adb=adb,
        artifact_root=artifact_root,
        deliverable_root=None,
        retry=1,
        max_retries=1,
    )

    with (
        patch(
            "game_agent.external_services.gameturbo.service.needs_initial_preprocess",
            return_value=True,
        ),
        patch(
            "game_agent.external_services.gameturbo.service._resolve_source_apk",
            return_value=source_apk,
        ),
        patch(
            "game_agent.external_services.gameturbo.service.run_bootstrap_from_source",
            return_value=bootstrap,
        ),
        patch(
            "game_agent.external_services.gameturbo.service.needs_gameturbo_deploy",
            return_value=True,
        ),
        patch(
            "game_agent.external_services.gameturbo.service.output_apk_path",
            return_value=output_apk,
        ),
        patch(
            "game_agent.external_services.gameturbo.retry.deploy_retry.run_deploy_with_ai_retry",
            deploy_mock,
        ),
        patch(
            "game_agent.external_services.gameturbo.service.get_apk_launch_info",
            return_value=ApkLaunchInfo(
                package_name="com.example.game",
                launch_activity=".Main",
            ),
        ),
    ):

        async def _prepare() -> object:
            return await GameTurboExternalService().prepare_installable(ctx)

        prepared = asyncio.run(_prepare())

    assert prepared is not None
    assert prepared.install_apk == output_apk.resolve()
    deploy_mock.assert_awaited_once()
