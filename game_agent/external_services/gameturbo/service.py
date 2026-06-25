from __future__ import annotations

import logging
from pathlib import Path

from game_agent.exceptions import DeployPhaseError
from game_agent.external_services.base import (
    ExternalEvidence,
    ExternalService,
    PreparedApp,
    RetryDecision,
)
from game_agent.external_services.context import ServiceContext
from game_agent.models.pipeline_phase import PipelinePhase
from game_agent.models.run_failure import RunFailure
from game_agent.models.task_runtime import TaskRuntimeRegistry
from game_agent.external_services.gameturbo.log import (
    GAMETURBO_LOG_COLLECTOR,
    bootstrap_gameturbo_log,
    clear_device_logcat,
    finalize_gameturbo_log,
)
from game_agent.services.external_log_base import ExternalLogCollector
from game_agent.utils.apk_util import get_apk_launch_info
from game_agent.core.apk_staging import parse_gid_from_apk_name, resolve_task_gid
from game_agent.external_services.gameturbo.bootstrap import (
    discover_source_apk,
    find_merged_config_for_deliverable,
    needs_gameturbo_deploy,
    needs_initial_preprocess,
    output_apk_path,
    resolve_existing_game_config,
    run_bootstrap_from_source,
)

logger = logging.getLogger(__name__)


def _deploy_gid(ctx: ServiceContext) -> str | None:
    gid = (
        resolve_task_gid(ctx.app_config.runtime.gid or "")
        or (ctx.app_config.runtime.gid or "").strip()
    )
    return gid or None


def _resolve_source_apk(ctx: ServiceContext) -> Path | None:
    runtime = ctx.app_config.runtime
    if runtime.source_apk is not None and runtime.source_apk.is_file():
        return runtime.source_apk.resolve()
    if (
        ctx.preprocess_record is not None
        and ctx.preprocess_record.processed_apk is not None
        and ctx.preprocess_record.processed_apk.is_file()
    ):
        return ctx.preprocess_record.processed_apk.resolve()
    try:
        return discover_source_apk(
            gid=_deploy_gid(ctx),
            source_apk=runtime.source_apk,
        )
    except RuntimeError:
        return None


class GameTurboExternalService(ExternalService):
    name = "gameturbo"

    def is_enabled(self, ctx: ServiceContext) -> bool:
        return bool(ctx.app_config.external_services.gameturbo.enabled)

    async def prepare_installable(self, ctx: ServiceContext) -> PreparedApp | None:
        deploy_gid = _deploy_gid(ctx)
        output_apk = output_apk_path(deploy_gid)
        runtime = ctx.app_config.runtime
        adb = ctx.adb
        gid: str
        game_config_path: Path

        if needs_initial_preprocess(deploy_gid):
            if ctx.audit is not None:
                ctx.audit.log_phase(
                    PipelinePhase.INIT.value,
                    "进入 GameTurbo 前置处理",
                    output_apk=str(output_apk),
                )
            source_apk = _resolve_source_apk(ctx)
            if source_apk is None:
                raise RuntimeError("缺少 source_apk，无法 bootstrap GameTurbo")
            result = run_bootstrap_from_source(source_apk, gid=deploy_gid)
            runtime.update_gameturbo(
                gid=result.gid,
                source_apk=result.source_apk,
                game_config_path=result.game_config_path,
            )
            TaskRuntimeRegistry.register(runtime)
            runtime.require_gameturbo()
            gid = result.gid
            game_config_path = result.game_config_path
            logger.info(
                "GameTurbo Init: gid=%s config=%s created=%s source=%s",
                result.gid,
                result.game_config_path,
                result.created_config,
                result.source_apk,
            )
            if ctx.audit is not None:
                ctx.audit.log_phase(
                    PipelinePhase.INIT.value,
                    "GameTurbo 配置已准备",
                    gid=result.gid,
                    game_config_path=str(result.game_config_path),
                    source_apk=str(result.source_apk),
                    created_config=result.created_config,
                )
        elif runtime.gid and runtime.game_config_path is not None:
            gid = runtime.gid
            game_config_path = runtime.game_config_path
            logger.info(
                "跳过 GameTurbo Init，使用已有上下文 gid=%s config=%s",
                gid,
                game_config_path,
            )
            if ctx.audit is not None:
                ctx.audit.log_phase(
                    PipelinePhase.INIT.value,
                    "跳过 bootstrap，复用 gameturbo 上下文",
                    gid=gid,
                    game_config_path=str(game_config_path),
                    output_apk=str(output_apk),
                )
        else:
            source_apk = discover_source_apk(
                gid=deploy_gid,
                source_apk=runtime.source_apk,
            )
            gid = parse_gid_from_apk_name(source_apk)
            game_config_path = resolve_existing_game_config(gid)
            runtime.update_gameturbo(
                gid=gid,
                source_apk=source_apk,
                game_config_path=game_config_path,
            )
            TaskRuntimeRegistry.register(runtime)
            logger.info(
                "已从现有 gameturbo 产物恢复上下文: gid=%s config=%s",
                gid,
                game_config_path,
            )
            if ctx.audit is not None:
                ctx.audit.log_phase(
                    PipelinePhase.INIT.value,
                    "恢复 GameTurbo 上下文",
                    gid=gid,
                    game_config_path=str(game_config_path),
                    source_apk=str(source_apk),
                    output_apk=str(output_apk),
                )

        target_pkg = ctx.app_config.game.package_name.strip()
        package_installed = bool(
            target_pkg and adb.is_package_installed(target_pkg),
        )
        skip_install = not needs_gameturbo_deploy(
            output_apk,
            package_installed=package_installed,
        )
        if skip_install:
            logger.info(
                "跳过 deploy：设备已安装 %s（产物 %s）",
                target_pkg,
                output_apk.name if output_apk.is_file() else "已清理/缺失",
            )
            if ctx.audit is not None:
                ctx.audit.log_phase(
                    PipelinePhase.INIT.value,
                    "跳过 deploy，设备已安装",
                    gid=gid,
                    package=target_pkg,
                    output_apk=str(output_apk),
                )
        else:
            if output_apk.is_file():
                logger.info(
                    "本地已有 %s 但设备未安装 %s，重新 deploy",
                    output_apk.name,
                    target_pkg or "(unknown)",
                )
            else:
                logger.info("缺少 deploy 产物，开始 GameTurbo deploy gid=%s", gid)
            from game_agent.external_services.gameturbo.retry.deploy_retry import (
                run_deploy_with_ai_retry_sync,
            )

            deploy_result = run_deploy_with_ai_retry_sync(
                ctx.app_config,
                gid=gid,
                game_config_path=game_config_path,
                artifact_root=ctx.artifact_root,
                audit=ctx.audit,
                phase=PipelinePhase.INIT.value,
            )
            if ctx.audit is not None:
                ctx.audit.log_phase(
                    PipelinePhase.INIT.value,
                    "GameTurbo deploy 已完成",
                    gid=gid,
                    deploy_log=str(deploy_result.log_path or ""),
                    output_apk=str(output_apk),
                )

        install_apk = output_apk if output_apk.is_file() else runtime.source_apk
        if install_apk is None or not install_apk.is_file():
            raise DeployPhaseError("GameTurbo deploy 后缺少可安装 APK")

        runtime.install_apk = install_apk.resolve()
        TaskRuntimeRegistry.register(runtime)
        info = get_apk_launch_info(install_apk)
        return PreparedApp(
            install_apk=install_apk.resolve(),
            source_apk=runtime.source_apk,
            package_name=info.package_name if info else target_pkg,
            launch_activity=info.launch_activity if info else "",
            skip_install=skip_install,
            prepared_by=self.name,
        )

    async def before_parallel_phase(self, ctx: ServiceContext) -> None:
        if not ctx.app_config.modules.log_monitor:
            return
        clear_device_logcat(ctx.adb)
        bootstrap_gameturbo_log(ctx.adb, ctx.artifact_root)
        logger.info(
            "[GameTurbo] 已 logcat -c 并采集本轮 GameTurbo 快照",
        )

    async def after_parallel_phase(self, ctx: ServiceContext) -> None:
        if ctx.app_config.modules.log_monitor:
            finalize_gameturbo_log(ctx.adb, ctx.artifact_root)

    def log_collector(self, ctx: ServiceContext) -> ExternalLogCollector | None:
        if not ctx.app_config.modules.log_monitor:
            return None
        return GAMETURBO_LOG_COLLECTOR

    async def on_failure(
        self,
        ctx: ServiceContext,
        failure: RunFailure,
        *,
        will_retry: bool,
    ) -> RetryDecision:
        if will_retry and failure.retryable:
            return RetryDecision(
                wants_plugin_retry=True,
                reason="GameTurbo Modify/deploy retry",
            )
        return RetryDecision()

    def collect_evidence(self, ctx: ServiceContext) -> ExternalEvidence | None:
        gid = (ctx.app_config.runtime.gid or "").strip()
        if not gid:
            return None
        config_path = find_merged_config_for_deliverable(
            gid,
            winning_artifact_root=ctx.artifact_root,
        )
        files: dict[str, str] = {}
        for name in ("gameturbo.log", "domain_region_analysis.json", "deploy.log"):
            p = ctx.artifact_root / name
            if p.is_file():
                files[name] = str(p.resolve())
        metadata: dict[str, object] = {}
        if config_path is not None and config_path.is_file():
            metadata["merged_config"] = str(config_path.resolve())
        if not files and not metadata:
            return None
        return ExternalEvidence(
            service_name=self.name,
            files=files,
            metadata=metadata,
        )

    def effective_log_monitor(self, ctx: ServiceContext, modules_log_monitor: bool) -> bool:
        return modules_log_monitor

    def effective_retry_config(self, ctx: ServiceContext, modules_retry: bool) -> bool:
        return modules_retry
