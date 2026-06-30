from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from game_agent.exceptions import (
    ConfigPatchGenerationError,
    ConfigPatchLlmError,
    ConfigPatchRejectedError,
    DeployPhaseError,
)
from game_agent.external_services.gameturbo.models.config import GameTurboConfigPatch
from game_agent.models.pipeline_phase import PipelinePhase
from game_agent.models.task_config import TaskConfig
from game_agent.external_services.gameturbo.retry.analysis import AnalysisAgent
from game_agent.external_services.gameturbo.retry.deploy_retry import run_deploy_with_ai_retry
from game_agent.services.adb_service import AdbService
from game_agent.external_services.gameturbo.config_retry import (
    infer_blocked_stage,
    prepare_modify_stage,
    record_patch_applied,
)
from game_agent.external_services.gameturbo.log import ensure_gameturbo_log_for_analysis
from game_agent.services.pipeline_trace import trace_operation
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.external_services.gameturbo.bootstrap import output_apk_path
from game_agent.external_services.gameturbo.config.apply import (
    ConfigApplyResult,
    apply_gameturbo_config_patch,
)
from game_agent.external_services.gameturbo.log.domain_extract import (
    DEFAULT_OUTPUT_NAME,
    extract_domain_region_from_log,
    load_domain_region_analysis_json,
)
from game_agent.utils.stage_logging import pipeline_stage

logger = logging.getLogger(__name__)


def _patch_has_actionable_changes(patch: GameTurboConfigPatch) -> bool:
    return bool(patch.direct_patterns or patch.port_rules)


def _nonempty_log_path(path: Path | None) -> Path | None:
    if path is None or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return path if text else None


@dataclass(slots=True)
class RetryConfigHandler:
    """配置与重试阶段：读取域名 JSON、AI 报告、deploy（仅 retry_on_failure 时）。"""

    adb: AdbService
    app_config: TaskConfig
    config_path: Path
    artifact_root: Path | None
    task_deliverable_root: Path | None = None
    blocked_stage_hint: str = ""
    audit: RunAuditLogger | None = None

    async def run(self, retry_count: int, reason: str) -> None:
        with pipeline_stage(
            PipelinePhase.MODIFY.value,
            artifact_root=self.artifact_root,
            note=f"modify retry after attempt {retry_count}",
            write_external_log_marker=True,
        ):
            await self._run_impl(retry_count, reason)

    async def _run_impl(self, retry_count: int, reason: str) -> None:
        logger.info("[RetryConfig] Config retry (attempt %d): %s", retry_count, reason[:200])
        runtime = self.app_config.runtime
        if self.audit is not None:
            self.audit.log_phase(
                PipelinePhase.MODIFY.value,
                "Config retry start",
                reason=reason[:2000],
                gid=runtime.gid,
                game_config_path=str(runtime.game_config_path or ""),
            )
        runtime.require_gameturbo()
        gid = runtime.gid
        game_config_path = runtime.game_config_path
        assert game_config_path is not None

        deliverable = self.task_deliverable_root
        blocked = self.blocked_stage_hint or infer_blocked_stage(reason=reason)
        backup_before = None
        restored_from: str | None = None
        if deliverable is not None:
            backup_before, restored_from = prepare_modify_stage(
                game_config_path,
                deliverable,
                failed_attempt=retry_count,
                artifact_root=self.artifact_root,
                blocked_stage_hint=blocked,
            )
            if self.audit is not None:
                self.audit.log_phase(
                    PipelinePhase.MODIFY.value,
                    "GameTurbo config backup/restore",
                    failed_attempt=retry_count,
                    next_attempt=retry_count + 1,
                    restored_from=restored_from or "",
                    backup_before=str(backup_before),
                    blocked_stage=blocked,
                )

        local_log_path = (
            self.artifact_root / "gameturbo.log"
            if self.artifact_root
            else Path("gameturbo.log")
        )
        domain_json = self._require_domain_analysis_json(local_log_path)

        with trace_operation("modify", "ai_propose_config_patch", gid=gid) as rec:
            patch = await self._run_ai_patch(
                reason=reason,
                local_log_path=local_log_path,
                domain_analysis=domain_json,
                game_config_path=game_config_path,
                failed_attempt=retry_count,
            )
            rec.ok(
                direct_patterns=len(patch.direct_patterns),
                port_rules=len(patch.port_rules),
            )

        self._require_actionable_patch(patch)

        with trace_operation("modify", "apply_config_patch", path=str(game_config_path)) as rec:
            apply_result = apply_gameturbo_config_patch(game_config_path, patch)
            rec.ok(changed=apply_result.changed, summary=apply_result.summary)

        logger.info("GameTurbo config patch apply: %s", apply_result.summary or ["no changes"])
        self._require_config_changed(apply_result, patch)

        if deliverable is not None and backup_before is not None:
            record_patch_applied(
                deliverable,
                failed_attempt=retry_count,
                game_config_path=game_config_path,
                patch=patch,
                apply_result=apply_result,
                restored_from=restored_from,
                backup_before_path=backup_before,
                artifact_root=self.artifact_root,
                blocked_stage_hint=blocked,
            )
        if self.audit is not None:
            self.audit.log_phase(
                PipelinePhase.MODIFY.value,
                "GameTurbo config patch applied",
                changed=apply_result.changed,
                patch=patch.model_dump(mode="json"),
                summary=apply_result.summary,
                path=str(apply_result.path),
            )

        with trace_operation("modify", "deploy_after_patch", gid=gid) as rec:
            try:
                deploy_result = await run_deploy_with_ai_retry(
                    self.app_config,
                    gid=gid,
                    game_config_path=game_config_path,
                    artifact_root=self.artifact_root,
                    audit=self.audit,
                    phase=PipelinePhase.MODIFY.value,
                )
                rec.ok(returncode=deploy_result.returncode, log_path=str(deploy_result.log_path))
            except DeployPhaseError as e:
                rec.fail(error=str(e)[:500])
                raise
        apk_path = output_apk_path(gid)
        if self.audit is not None:
            self.audit.log_phase(
                PipelinePhase.MODIFY.value,
                "deploy.sh finished",
                gid=gid,
                deploy_log=str(deploy_result.log_path or ""),
                output_apk=str(apk_path),
            )

        if self.audit is not None:
            self.audit.log_phase(PipelinePhase.MODIFY.value, "Config retry stage done")

    def _require_domain_analysis_json(self, local_log_path: Path) -> dict:
        if not self.artifact_root:
            raise ConfigPatchGenerationError(
                "missing artifact_root, cannot build domain_region_analysis.json",
                stage="domain_analysis",
            )
        json_path = self.artifact_root / DEFAULT_OUTPUT_NAME
        existing = load_domain_region_analysis_json(json_path)
        if existing is not None:
            return existing
        analysis_log = _nonempty_log_path(
            ensure_gameturbo_log_for_analysis(self.artifact_root),
        )
        if analysis_log is None:
            analysis_log = _nonempty_log_path(local_log_path)
        if analysis_log is None:
            raise ConfigPatchGenerationError(
                "missing valid gameturbo.log for domain/region analysis",
                stage="domain_analysis",
            )
        try:
            with trace_operation(
                "domain_extract",
                "ensure_domain_analysis_json",
                log_path=str(analysis_log),
            ) as rec:
                result = extract_domain_region_from_log(
                    analysis_log,
                    output_path=json_path,
                )
                rec.ok(domain_count=result.domain_count)
            return result.to_json_dict()
        except Exception as e:
            logger.error("Domain/region analysis failed: %s", e)
            raise ConfigPatchGenerationError(
                f"Domain/region analysis failed, cannot enter Modify: {e}",
                stage="domain_analysis",
            ) from e

    def _require_actionable_patch(self, patch: GameTurboConfigPatch) -> None:
        if _patch_has_actionable_changes(patch):
            return
        analysis = (patch.analysis or "").strip()
        summary = analysis[:1500] if analysis else "AI gave no reason"
        raise ConfigPatchRejectedError(
            "AI found no safe config changes for current log/domains (direct_patterns/port_rules empty)",
            analysis=summary,
        )

    def _require_config_changed(
        self,
        apply_result: ConfigApplyResult,
        patch: GameTurboConfigPatch,
    ) -> None:
        if apply_result.changed:
            return
        raise ConfigPatchGenerationError(
            "Config patch applied with no changes (duplicates or invalid rules), "
            f"summary={apply_result.summary or []}",
            stage="noop_patch",
        )

    async def _run_ai_patch(
        self,
        *,
        reason: str,
        local_log_path: Path,
        domain_analysis: dict,
        game_config_path: Path,
        failed_attempt: int,
    ) -> GameTurboConfigPatch:
        max_attempts = self.app_config.gameturbo.modify_patch_max_llm_retries
        last_llm_error: ConfigPatchLlmError | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                patch = await self._invoke_ai_patch_once(
                    reason=reason,
                    local_log_path=local_log_path,
                    domain_analysis=domain_analysis,
                    game_config_path=game_config_path,
                    failed_attempt=failed_attempt,
                )
            except ConfigPatchLlmError as exc:
                last_llm_error = exc
                logger.warning(
                    "Modify AI request failed (%d/%d): %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                if self.audit is not None:
                    self.audit.log_phase(
                        PipelinePhase.MODIFY.value,
                        "AI config patch request failed",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        error=str(exc)[:800],
                    )
                if attempt < max_attempts:
                    continue
                raise ConfigPatchLlmError(
                    f"Modify stage AI request failed after {max_attempts} attempts: {exc}",
                    attempt=max_attempts,
                    max_attempts=max_attempts,
                ) from exc
            else:
                if attempt > 1:
                    logger.info("Modify AI request succeeded on attempt %d", attempt)
                return patch

        if last_llm_error is not None:
            raise last_llm_error
        raise ConfigPatchLlmError(
            "Modify stage AI request failed",
            attempt=max_attempts,
            max_attempts=max_attempts,
        )

    async def _invoke_ai_patch_once(
        self,
        *,
        reason: str,
        local_log_path: Path,
        domain_analysis: dict,
        game_config_path: Path,
        failed_attempt: int,
    ) -> GameTurboConfigPatch:
        cfg = self.app_config
        logger.info("AI log re-analysis for config patch...")
        agent = AnalysisAgent(cfg.llm, deepseek=cfg.deepseek)
        log_content = ""
        if local_log_path.is_file():
            try:
                log_content = local_log_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        from game_agent.services.anomaly_evidence import (
            format_anomaly_evidence_for_ai,
            load_anomaly_evidence,
        )

        screen_context = format_anomaly_evidence_for_ai(
            load_anomaly_evidence(self.artifact_root),
        )
        if self.artifact_root and cfg.llm_multimodal is not None and not screen_context:
            from game_agent.services.vision_context import summarize_monitor_screenshots

            shots = sorted(self.artifact_root.glob("monitor_screen_*.png"))
            screen_context = await summarize_monitor_screenshots(
                cfg.llm_multimodal,
                shots,
                max_images=3,
            )

        try:
            import json

            current_config = json.loads(game_config_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"Failed to read GameTurbo config: {game_config_path}: {e}") from e

        patch = await agent.analyze_and_propose_patch(
            anomaly_reason=reason,
            log_content=log_content,
            current_config=current_config,
            domain_analysis=domain_analysis,
            screen_context=screen_context,
            blocked_stage_hint=self.blocked_stage_hint,
            prior_patch_restored=bool(self.task_deliverable_root and failed_attempt >= 1),
        )
        logger.info("AI config patch:\n%s", patch.model_dump_json(indent=2))
        if self.audit is not None:
            self.audit.log_phase(
                PipelinePhase.MODIFY.value,
                "AI config patch generated",
                analysis=patch.analysis[:4000],
                patch=patch.model_dump(mode="json"),
            )
        if self.artifact_root:
            (self.artifact_root / "ai_analysis_report.txt").write_text(
                patch.model_dump_json(indent=2),
                encoding="utf-8",
            )
        return patch
