from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from game_agent.exceptions import DeployPhaseError
from game_agent.models.gameturbo_config import GameTurboConfigPatch
from game_agent.models.pipeline_phase import PipelinePhase
from game_agent.models.task_config import TaskConfig
from game_agent.modules.retry.analysis import AnalysisAgent
from game_agent.modules.retry.deploy_retry import run_deploy_with_ai_retry
from game_agent.services.adb_service import AdbService
from game_agent.services.gameturbo_config_retry import (
    infer_blocked_stage,
    prepare_modify_stage,
    record_patch_applied,
)
from game_agent.services.gameturbo_log import ensure_gameturbo_log_for_analysis
from game_agent.services.pipeline_trace import trace_operation
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.utils.gameturbo_bootstrap import output_apk_path
from game_agent.utils.gameturbo_config_apply import apply_gameturbo_config_patch
from game_agent.utils.gameturbo_log_domain_extract import (
    DEFAULT_OUTPUT_NAME,
    extract_domain_region_from_log,
    load_domain_region_analysis_json,
)

logger = logging.getLogger(__name__)


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
        logger.info("[RetryConfig] 配置与重试 (第 %d 次): %s", retry_count, reason[:200])
        runtime = self.app_config.runtime
        if self.audit is not None:
            self.audit.log_phase(
                PipelinePhase.MODIFY.value,
                "进入配置与重试",
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
                    "GameTurbo 配置备份/恢复",
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
        domain_json = self._ensure_domain_analysis_json(local_log_path)
        if domain_json is None:
            logger.warning(
                "[RetryConfig] 缺少 domain_region_analysis.json，跳过 AI 配置补丁",
            )
            with trace_operation("modify", "skip_ai_patch_no_domain_json") as rec:
                rec.skip(message="domain_region_analysis.json 缺失")
            patch = GameTurboConfigPatch(
                analysis=(
                    "未生成 domain_region_analysis.json（extract_domain_region_from_log 未成功），"
                    "本轮不修改 direct_patterns/port_rules。"
                ),
            )
            with trace_operation("modify", "apply_config_patch", skipped=True) as rec:
                apply_result = apply_gameturbo_config_patch(game_config_path, patch)
                rec.ok(changed=apply_result.changed, summary=apply_result.summary)
            logger.info("GameTurbo 配置补丁应用结果: %s", apply_result.summary or ["无变更"])
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
                    "域名区域分析缺失，已跳过 AI 补丁",
                    changed=apply_result.changed,
                )
        else:
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
            with trace_operation("modify", "apply_config_patch", path=str(game_config_path)) as rec:
                apply_result = apply_gameturbo_config_patch(game_config_path, patch)
                rec.ok(changed=apply_result.changed, summary=apply_result.summary)
            logger.info("GameTurbo 配置补丁应用结果: %s", apply_result.summary or ["无变更"])
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
                    "GameTurbo 配置补丁已处理",
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
                "deploy.sh 已执行",
                gid=gid,
                deploy_log=str(deploy_result.log_path or ""),
                output_apk=str(apk_path),
            )

        if self.audit is not None:
            self.audit.log_phase(PipelinePhase.MODIFY.value, "配置与重试阶段完成")

    def _ensure_domain_analysis_json(self, local_log_path: Path) -> dict | None:
        if not self.artifact_root:
            return None
        json_path = self.artifact_root / DEFAULT_OUTPUT_NAME
        existing = load_domain_region_analysis_json(json_path)
        if existing is not None:
            return existing
        analysis_log = ensure_gameturbo_log_for_analysis(self.artifact_root)
        if analysis_log is None:
            analysis_log = local_log_path if local_log_path.is_file() else None
        if analysis_log is None or not analysis_log.is_file():
            return None
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
            logger.warning("域名/区域分析失败: %s", e)
            return None

    async def _run_ai_patch(
        self,
        *,
        reason: str,
        local_log_path: Path,
        domain_analysis: dict | None,
        game_config_path: Path,
        failed_attempt: int,
    ) -> GameTurboConfigPatch:
        cfg = self.app_config
        logger.info("AI 二次日志分析并生成配置补丁...")
        agent = AnalysisAgent(cfg.llm, deepseek=cfg.deepseek)
        log_content = ""
        if local_log_path.is_file():
            try:
                log_content = local_log_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        screen_context = ""
        if self.artifact_root and cfg.llm_multimodal is not None:
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
            raise RuntimeError(f"读取 GameTurbo 配置失败: {game_config_path}: {e}") from e

        patch = await agent.analyze_and_propose_patch(
            anomaly_reason=reason,
            log_content=log_content,
            current_config=current_config,
            domain_analysis=domain_analysis,
            screen_context=screen_context,
            blocked_stage_hint=self.blocked_stage_hint,
            prior_patch_restored=bool(self.task_deliverable_root and failed_attempt >= 1),
        )
        logger.info("AI 配置补丁:\n%s", patch.model_dump_json(indent=2))
        if self.audit is not None:
            self.audit.log_phase(
                PipelinePhase.MODIFY.value,
                "AI 配置补丁生成完成",
                analysis=patch.analysis[:4000],
                patch=patch.model_dump(mode="json"),
            )
        if self.artifact_root:
            (self.artifact_root / "ai_analysis_report.txt").write_text(
                patch.model_dump_json(indent=2),
                encoding="utf-8",
            )
        return patch
