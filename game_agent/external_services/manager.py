from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from game_agent.external_services.base import (
    ExternalEvidence,
    PreparedApp,
)
from game_agent.external_services.gameturbo.service import GameTurboExternalService

if TYPE_CHECKING:
    from game_agent.external_services.context import ServiceContext
    from game_agent.models.settings import AppConfig
    from game_agent.models.task_config import TaskConfig
    from game_agent.models.task_runtime import TaskRuntime
    from game_agent.modules.preprocessing.preprocessor import PreprocessResult
    from game_agent.services.adb_service import AdbService
    from game_agent.services.external_log_base import ExternalLogCollector
    from game_agent.services.run_audit_log import RunAuditLogger

logger = logging.getLogger(__name__)

CORE_EXECUTION_LOG_FILES: tuple[str, ...] = (
    "process.log",
    "pipeline_trace.jsonl",
    "deploy.log",
)

CORE_ANALYSIS_LOG_FILES: tuple[str, ...] = (
    "attempt_failure_report.md",
    "attempt_failure_report.json",
    "ai_analysis_report.txt",
    "anomaly_evidence.json",
)

GAMETURBO_EXECUTION_LOG_FILES: tuple[str, ...] = ("gameturbo.log",)
GAMETURBO_ANALYSIS_LOG_FILES: tuple[str, ...] = ("domain_region_analysis.json",)
GAMETURBO_SESSION_LOG_GLOB = "gameturbo_session_*.log"


@dataclass(frozen=True, slots=True)
class ExecutionLogArchivePlan:
    execution_files: tuple[str, ...]
    analysis_files: tuple[str, ...]
    session_log_glob: str | None = None
    prepare_artifact: Callable[[Path], None] | None = None


def core_execution_log_archive_plan() -> ExecutionLogArchivePlan:
    return ExecutionLogArchivePlan(
        execution_files=CORE_EXECUTION_LOG_FILES,
        analysis_files=CORE_ANALYSIS_LOG_FILES,
    )


class ExternalServiceManager:
  """Registers and dispatches enabled external service plugins."""

  def __init__(self, cfg: AppConfig) -> None:
      self._cfg = cfg
      self._services = [GameTurboExternalService()]

  def gameturbo_enabled(self) -> bool:
      return bool(self._cfg.external_services.gameturbo.enabled)

  def _active(self, ctx: ServiceContext):
      for svc in self._services:
          if svc.is_enabled(ctx):
              yield svc

  async def prepare_installable(self, ctx: ServiceContext) -> PreparedApp | None:
      for svc in self._active(ctx):
          prepared = await svc.prepare_installable(ctx)
          if prepared is not None:
              logger.info(
                  "[ExternalServices] %s prepared installable apk=%s",
                  svc.name,
                  prepared.install_apk.name,
              )
              return prepared
      return None

  async def before_install(self, ctx: ServiceContext, prepared: PreparedApp) -> None:
      for svc in self._active(ctx):
          await svc.before_install(ctx, prepared)

  async def after_install(self, ctx: ServiceContext, prepared: PreparedApp) -> None:
      for svc in self._active(ctx):
          await svc.after_install(ctx, prepared)

  async def before_parallel_phase(self, ctx: ServiceContext) -> None:
      for svc in self._active(ctx):
          await svc.before_parallel_phase(ctx)

  async def after_parallel_phase(self, ctx: ServiceContext) -> None:
      for svc in self._active(ctx):
          await svc.after_parallel_phase(ctx)

  def collect_all_evidence(self, ctx: ServiceContext) -> dict[str, ExternalEvidence]:
      out: dict[str, ExternalEvidence] = {}
      for svc in self._active(ctx):
          evidence = svc.collect_evidence(ctx)
          if evidence is not None:
              out[svc.name] = evidence
      return out

  def external_log_collector(self, ctx: ServiceContext) -> ExternalLogCollector | None:
      for svc in self._active(ctx):
          collector = svc.log_collector(ctx)
          if collector is not None:
              return collector
      return None

  def effective_log_monitor(self, ctx: ServiceContext) -> bool:
      if not ctx.app_config.modules.log_monitor:
          return False
      return self.gameturbo_enabled()

  def effective_retry_config(self, ctx: ServiceContext) -> bool:
      if not ctx.app_config.modules.retry_on_failure:
          return False
      return self.gameturbo_enabled()

  def resolve_log_reader(self):
      if not self.gameturbo_enabled():
          return None
      from game_agent.external_services.gameturbo.log import (
          format_latest_gameturbo_log_for_agent,
      )

      return format_latest_gameturbo_log_for_agent

  def execution_log_archive_plan(self) -> ExecutionLogArchivePlan:
      if not self.gameturbo_enabled():
          return core_execution_log_archive_plan()

      def _prepare(artifact_root: Path) -> None:
          from game_agent.external_services.gameturbo.log import (
              ensure_gameturbo_log_for_analysis,
          )

          ensure_gameturbo_log_for_analysis(artifact_root)

      return ExecutionLogArchivePlan(
          execution_files=CORE_EXECUTION_LOG_FILES + GAMETURBO_EXECUTION_LOG_FILES,
          analysis_files=CORE_ANALYSIS_LOG_FILES + GAMETURBO_ANALYSIS_LOG_FILES,
          session_log_glob=GAMETURBO_SESSION_LOG_GLOB,
          prepare_artifact=_prepare,
      )

  def apply_preprocess_context(
      self,
      *,
      runtime: TaskRuntime,
      processed_apk: Path,
  ) -> None:
      if not self.gameturbo_enabled():
          return
      from game_agent.external_services.gameturbo.orchestration import (
          apply_preprocess_context,
      )

      apply_preprocess_context(runtime=runtime, processed_apk=processed_apk)

  def sync_runtime_from_packages(
      self,
      *,
      runtime: TaskRuntime,
      deploy_gid: str | None,
  ) -> None:
      if not self.gameturbo_enabled():
          return
      from game_agent.external_services.gameturbo.orchestration import (
          sync_runtime_from_packages,
      )

      sync_runtime_from_packages(runtime=runtime, deploy_gid=deploy_gid)

  def preprocessing_packages_dir(self) -> Path | None:
      if not self.gameturbo_enabled():
          return None
      from game_agent.external_services.gameturbo.orchestration import (
          preprocessing_packages_dir,
      )

      return preprocessing_packages_dir()

  def infer_blocked_stage(
      self,
      *,
      reason: str,
      ui_stage: str,
      ui_progress: str,
  ) -> str:
      if not self.gameturbo_enabled():
          return ui_stage or "unknown"
      from game_agent.external_services.gameturbo.orchestration import infer_blocked_stage

      return infer_blocked_stage(
          reason=reason,
          ui_stage=ui_stage,
          ui_progress=ui_progress,
      )

  def format_executor_retry_brief(self, deliverable_root: Path) -> str:
      if not self.gameturbo_enabled():
          return ""
      from game_agent.external_services.gameturbo.orchestration import (
          format_executor_retry_brief,
      )

      return format_executor_retry_brief(deliverable_root)

  def resolve_source_apk(
      self,
      *,
      runtime: TaskRuntime,
      deploy_gid: str | None,
      preprocess_record: PreprocessResult | None,
  ) -> Path | None:
      if not self.gameturbo_enabled():
          return None
      from game_agent.external_services.gameturbo.orchestration import (
          resolve_orchestrator_source_apk,
      )

      return resolve_orchestrator_source_apk(
          runtime=runtime,
          deploy_gid=deploy_gid,
          preprocess_record=preprocess_record,
      )

  def require_success_merged_config(
      self,
      *,
      deploy_gid: str | None,
      winning_artifact_root: Path,
  ) -> Path:
      if not self.gameturbo_enabled():
          raise RuntimeError("GameTurbo plugin disabled, cannot resolve merge config")
      from game_agent.external_services.gameturbo.orchestration import (
          require_success_merged_config,
      )

      return require_success_merged_config(
          deploy_gid=deploy_gid,
          winning_artifact_root=winning_artifact_root,
      )

  def cleanup_deploy_artifacts(self, *, gid: str | None) -> list[str]:
      if not self.gameturbo_enabled():
          return []
      from game_agent.external_services.gameturbo.orchestration import (
          cleanup_deploy_artifacts_for_gid,
      )

      return cleanup_deploy_artifacts_for_gid(gid)

  def finalize_task_packages(
      self,
      *,
      gid: str,
      source_apk: Path | None,
  ) -> dict[str, list[str]]:
      if not self.gameturbo_enabled():
          return {}
      from game_agent.external_services.gameturbo.orchestration import finalize_task_packages

      return finalize_task_packages(gid=gid, source_apk=source_apk)

  async def run_plugin_failure_cleanup(
      self,
      *,
      adb: AdbService,
      app_config: TaskConfig | AppConfig,
      artifact_root: Path | None,
      audit: RunAuditLogger | None,
  ) -> None:
      if not self.gameturbo_enabled():
          return
      from game_agent.external_services.gameturbo.retry.cleanup import (
          run_gameturbo_failure_cleanup,
      )

      await run_gameturbo_failure_cleanup(
          adb=adb,
          app_config=app_config,
          artifact_root=artifact_root,
          audit=audit,
      )

  async def run_modify_retry(
      self,
      ctx: ServiceContext,
      *,
      retry_count: int,
      failure_message: str,
      config_path: Path,
      deliverable_root: Path | None,
      blocked_stage_hint: str,
      audit: RunAuditLogger | None,
  ) -> None:
      if not self.effective_retry_config(ctx):
          return
      from game_agent.external_services.gameturbo.retry.modify import RetryConfigHandler

      handler = RetryConfigHandler(
          adb=ctx.adb,
          app_config=ctx.app_config,
          config_path=config_path,
          artifact_root=ctx.artifact_root,
          task_deliverable_root=deliverable_root,
          blocked_stage_hint=blocked_stage_hint,
          audit=audit,
      )
      await handler.run(retry_count, failure_message)

  def failure_deliverable_files(self) -> tuple[str, ...]:
      from game_agent.external_services.gameturbo.deliverables import (
          failure_deliverable_files,
      )

      return failure_deliverable_files(gameturbo_enabled=self.gameturbo_enabled())

  def failure_session_log_glob(self) -> str | None:
      if not self.gameturbo_enabled():
          return None
      from game_agent.external_services.gameturbo.deliverables import (
          GAMETURBO_SESSION_LOG_GLOB,
      )

      return GAMETURBO_SESSION_LOG_GLOB
