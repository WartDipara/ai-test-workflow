from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from typing import TYPE_CHECKING

from game_agent.models.pipeline_phase import PipelinePhase
from game_agent.models.task_config import TaskConfig
from game_agent.services.adb_service import AdbService
from game_agent.services.device_workspace_cleanup import remove_leftover_game_installations
from game_agent.services.pipeline_trace import trace_operation
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.utils.stage_logging import pipeline_stage

if TYPE_CHECKING:
    from game_agent.external_services.manager import ExternalServiceManager
    from game_agent.models.settings import AppConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FailureCleanup:
    """失败收尾：截图/审计已由编排器处理；此处卸载游戏并触发插件清理。"""

    adb: AdbService
    app_config: TaskConfig
    artifact_root: Path | None
    audit: RunAuditLogger | None = None
    external_services: ExternalServiceManager | None = None

    async def run(self, reason: str) -> None:
        plugin_enabled = self.app_config.external_services.gameturbo.enabled
        with pipeline_stage(
            PipelinePhase.CLEANUP.value,
            artifact_root=self.artifact_root,
            note="failure cleanup start",
            write_external_log_marker=plugin_enabled,
        ):
            await self._run_impl(reason)

    async def _run_impl(self, reason: str) -> None:
        cfg = self.app_config
        logger.error("[FailureCleanup] 失败收尾: %s", reason)
        if self.audit is not None:
            self.audit.log_phase(
                PipelinePhase.CLEANUP.value,
                "开始失败收尾",
                reason=reason[:2000],
                gid=self.app_config.runtime.gid,
                game_config_path=str(self.app_config.runtime.game_config_path or ""),
            )

        if self.external_services is not None:
            await self.external_services.run_plugin_failure_cleanup(
                adb=self.adb,
                app_config=self.app_config,
                artifact_root=self.artifact_root,
                audit=self.audit,
            )
        elif cfg.external_services.gameturbo.enabled:
            from game_agent.external_services.gameturbo.retry.cleanup import (
                run_gameturbo_failure_cleanup,
            )

            await run_gameturbo_failure_cleanup(
                adb=self.adb,
                app_config=cfg,
                artifact_root=self.artifact_root,
                audit=self.audit,
            )

        game_pkg = (cfg.runtime.package_name or "").strip()
        packages = [game_pkg] if game_pkg else []
        with trace_operation("cleanup", "uninstall_game_if_present", packages=packages) as rec:
            results = remove_leftover_game_installations(self.adb, packages)
            uninstalled = [r.package for r in results if r.was_installed]
            rec.ok(uninstalled=uninstalled, checked=len(results))
        if self.audit is not None:
            if game_pkg:
                self.audit.log_phase(
                    PipelinePhase.CLEANUP.value,
                    "已处理设备游戏包（force-stop + 卸载若已安装）",
                    package=game_pkg,
                )
            else:
                logger.warning("TaskRuntime.package_name 为空，跳过卸载")
                self.audit.log_phase(PipelinePhase.CLEANUP.value, "跳过卸载（包名为空）")

        if self.audit is not None:
            self.audit.log_phase(PipelinePhase.CLEANUP.value, "失败收尾完成")
