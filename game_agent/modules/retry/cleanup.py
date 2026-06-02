from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from game_agent.models.pipeline_phase import PipelinePhase
from game_agent.models.settings import AppConfig
from game_agent.services.adb_service import AdbService
from game_agent.services.gameturbo_log import (
    ensure_gameturbo_log_for_analysis,
    finalize_gameturbo_log,
)
from game_agent.services.pipeline_trace import trace_operation
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.utils.gameturbo_log_domain_extract import (
    DEFAULT_OUTPUT_NAME,
    extract_domain_region_from_log,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FailureCleanup:
    """失败收尾：导出 GameTurbo 日志、结束游戏进程、卸载游戏。"""

    adb: AdbService
    app_config: AppConfig
    artifact_root: Path | None
    audit: RunAuditLogger | None = None

    async def run(self, reason: str) -> None:
        cfg = self.app_config
        logger.error("[FailureCleanup] 失败收尾: %s", reason)
        if self.audit is not None:
            self.audit.log_phase(
                PipelinePhase.CLEANUP.value,
                "开始失败收尾",
                reason=reason[:2000],
                gid=self.app_config.gameturbo.gid,
                game_config_path=str(self.app_config.gameturbo.game_config_path or ""),
            )

        with trace_operation("cleanup", "export_gameturbo_log") as rec:
            local_log_path = await self._export_gameturbo_log()
            rec.ok(
                path=str(local_log_path) if local_log_path else None,
                exists=local_log_path.is_file() if local_log_path else False,
            )
        if self.audit is not None:
            self.audit.log_phase(
                PipelinePhase.CLEANUP.value,
                "日志已导出",
                local_path=str(local_log_path) if local_log_path else None,
            )

        if self.artifact_root is not None:
            analysis_log = ensure_gameturbo_log_for_analysis(self.artifact_root)
        else:
            analysis_log = local_log_path

        if analysis_log is not None and self.artifact_root:
            analysis_json = self.artifact_root / DEFAULT_OUTPUT_NAME
            try:
                with trace_operation(
                    "domain_extract",
                    "extract_domain_region_from_log",
                    log_path=str(analysis_log),
                    output_path=str(analysis_json),
                ) as rec:
                    result = extract_domain_region_from_log(
                        analysis_log,
                        output_path=analysis_json,
                    )
                    rec.ok(
                        domain_count=result.domain_count,
                        tunnel=len(result.tunnel_domains),
                        direct=len(result.direct_domains),
                        unknown=len(result.unknown_domains),
                        unmatched_pending=len(result.unmatched_pending_ips),
                    )
                logger.info(
                    "域名/区域分析完成: %d 个域名, tunnel=%d direct=%d unknown=%d",
                    result.domain_count,
                    len(result.tunnel_domains),
                    len(result.direct_domains),
                    len(result.unknown_domains),
                )
                if self.audit is not None:
                    self.audit.log_phase(
                        PipelinePhase.CLEANUP.value,
                        "域名区域分析已写入 JSON",
                        path=str(analysis_json),
                        domain_count=result.domain_count,
                        non_china_domains=result.non_china_domains,
                    )
            except Exception as e:
                logger.warning("域名/区域分析失败: %s", e)
                if self.audit is not None:
                    self.audit.log_phase(
                        PipelinePhase.CLEANUP.value,
                        "域名区域分析失败",
                        error=str(e)[:500],
                    )

        game_pkg = (cfg.game.package_name or "").strip()
        packages = [game_pkg] if game_pkg else []
        with trace_operation("cleanup", "force_stop_packages", packages=packages) as rec:
            logger.info("中止游戏进程: %s", packages)
            self.adb.force_stop_packages(packages)
            rec.ok()
        if self.audit is not None:
            self.audit.log_phase(
                PipelinePhase.CLEANUP.value,
                "已 force-stop",
                packages=packages,
            )

        if game_pkg:
            with trace_operation("cleanup", "uninstall_game", package=game_pkg) as rec:
                logger.info("卸载游戏: %s", game_pkg)
                out = self.adb.uninstall(game_pkg)
                logger.info("%s", out)
                rec.ok(output=str(out)[:500])
            if self.audit is not None:
                self.audit.log_phase(
                    PipelinePhase.CLEANUP.value,
                    "已卸载游戏",
                    package=game_pkg,
                )
        else:
            logger.warning("game.package_name 为空，跳过卸载")
            if self.audit is not None:
                self.audit.log_phase(PipelinePhase.CLEANUP.value, "跳过卸载（包名为空）")

        if self.audit is not None:
            self.audit.log_phase(PipelinePhase.CLEANUP.value, "失败收尾完成")

    async def _export_gameturbo_log(self) -> Path | None:
        if self.artifact_root is None:
            return None
        logger.info("归档 GameTurbo 日志（合并设备缓冲区）...")
        return finalize_gameturbo_log(self.adb, self.artifact_root)
