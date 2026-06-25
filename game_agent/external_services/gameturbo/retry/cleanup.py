"""GameTurbo plugin failure cleanup (log export, domain analysis)."""

from __future__ import annotations

import logging
from pathlib import Path

from game_agent.models.pipeline_phase import PipelinePhase
from game_agent.models.task_config import TaskConfig
from game_agent.services.adb_service import AdbService
from game_agent.external_services.gameturbo.log import (
    ensure_gameturbo_log_for_analysis,
    finalize_gameturbo_log,
)
from game_agent.services.pipeline_trace import trace_operation
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.external_services.gameturbo.log.domain_extract import (
    DEFAULT_OUTPUT_NAME,
    extract_domain_region_from_log,
)

logger = logging.getLogger(__name__)


async def run_gameturbo_failure_cleanup(
    *,
    adb: AdbService,
    app_config: TaskConfig,
    artifact_root: Path | None,
    audit: RunAuditLogger | None,
) -> None:
    if artifact_root is None:
        return
    if not app_config.external_services.gameturbo.enabled:
        return

    with trace_operation("cleanup", "export_plugin_log") as rec:
        local_log_path = finalize_gameturbo_log(adb, artifact_root)
        rec.ok(
            path=str(local_log_path) if local_log_path else None,
            exists=local_log_path.is_file() if local_log_path else False,
        )
    if audit is not None:
        audit.log_phase(
            PipelinePhase.CLEANUP.value,
            "插件日志已导出",
            local_path=str(local_log_path) if local_log_path else None,
        )

    analysis_log = ensure_gameturbo_log_for_analysis(artifact_root)
    if analysis_log is None:
        return

    analysis_json = artifact_root / DEFAULT_OUTPUT_NAME
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
        if audit is not None:
            audit.log_phase(
                PipelinePhase.CLEANUP.value,
                "域名区域分析已写入 JSON",
                path=str(analysis_json),
                domain_count=result.domain_count,
                non_china_domains=result.non_china_domains,
            )
    except Exception as e:
        logger.warning("域名/区域分析失败: %s", e)
        if audit is not None:
            audit.log_phase(
                PipelinePhase.CLEANUP.value,
                "域名区域分析失败",
                error=str(e)[:500],
            )
