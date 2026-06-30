from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from game_agent.models.run_failure import RunFailure, user_interrupt_failure
from game_agent.models.task_config import TaskConfig
from game_agent.modules.retry.cleanup import FailureCleanup
from game_agent.services.adb_service import AdbService
from game_agent.services.failure_report import generate_and_save_attempt_failure_report
from game_agent.services.pipeline_trace import trace_operation
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.services.shutdown import is_shutdown_requested

if TYPE_CHECKING:
    from game_agent.external_services.context import ServiceContext
    from game_agent.external_services.manager import ExternalServiceManager

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AnomalyHandler:
    """异常处理入口：失败收尾（始终）+ 配置与重试（仅 retryable E2xxx）。"""

    adb: AdbService
    app_config: TaskConfig
    config_path: Path
    artifact_root: Path | None
    task_deliverable_root: Path | None = None
    blocked_stage_hint: str = ""
    audit: RunAuditLogger | None = None
    external_services: ExternalServiceManager | None = None
    service_context: ServiceContext | None = None

    async def handle(
        self,
        retry_count: int,
        failure: RunFailure,
        *,
        run_retry_config: bool,
        will_retry: bool,
    ) -> None:
        if is_shutdown_requested():
            failure = user_interrupt_failure()
            will_retry = False
        reason = failure.format()
        with trace_operation(
            "anomaly",
            "failure_cleanup",
            code=failure.code.value,
            retryable=failure.retryable,
            reason=reason[:200],
        ) as rec:
            cleanup = FailureCleanup(
                adb=self.adb,
                app_config=self.app_config,
                artifact_root=self.artifact_root,
                audit=self.audit,
                external_services=self.external_services,
            )
            await cleanup.run(reason)
            rec.ok()

        if not will_retry:
            if not failure.retryable:
                logger.error(
                    "[AnomalyHandler] Non-retryable %s, skip Modify/deploy retry",
                    failure.code.value,
                )
            elif not run_retry_config:
                logger.info("[AnomalyHandler] retry_on_failure=false, skip config retry")
            else:
                logger.info("[AnomalyHandler] Max retries reached, skip config retry")
            if not is_shutdown_requested():
                await self._write_attempt_failure_report(
                    retry_count,
                    failure,
                    will_retry=False,
                )
            else:
                logger.info("[AnomalyHandler] User interrupt, skip AI failure report")
            return

        if is_shutdown_requested():
            logger.info("[AnomalyHandler] User interrupt, skip Modify retry and AI report")
            return

        with trace_operation("anomaly", "retry_config_modify", retry=retry_count) as rec:
            assert self.external_services is not None
            assert self.service_context is not None
            await self.external_services.run_modify_retry(
                self.service_context,
                retry_count=retry_count,
                failure_message=failure.message,
                config_path=self.config_path,
                deliverable_root=self.task_deliverable_root,
                blocked_stage_hint=self.blocked_stage_hint,
                audit=self.audit,
            )
            rec.ok()
        if not is_shutdown_requested():
            await self._write_attempt_failure_report(retry_count, failure, will_retry=True)

    async def _write_attempt_failure_report(
        self,
        retry_count: int,
        failure: RunFailure,
        *,
        will_retry: bool,
    ) -> None:
        if self.artifact_root is None:
            return
        gid = (self.app_config.runtime.gid or "").strip() or "unknown"
        reason = failure.format()
        try:
            with trace_operation(
                "failure_report",
                "attempt_failure_report",
                retry=retry_count,
                will_retry=will_retry,
                code=failure.code.value,
            ) as rec:
                await generate_and_save_attempt_failure_report(
                    self.app_config,
                    retry_no=retry_count,
                    artifact_root=self.artifact_root,
                    reason=reason,
                    gid=gid,
                    will_retry=will_retry,
                    game_config_path=self.app_config.runtime.game_config_path,
                )
                rec.ok(path=str(self.artifact_root / "attempt_failure_report.md"))
            if self.audit is not None:
                self.audit.log_phase(
                    "failure_report",
                    "Attempt AI failure report generated",
                    path=str(self.artifact_root / "attempt_failure_report.md"),
                    will_retry=will_retry,
                    error_code=failure.code.value,
                )
        except Exception as e:
            logger.warning("Attempt AI failure report failed: %s", e)
