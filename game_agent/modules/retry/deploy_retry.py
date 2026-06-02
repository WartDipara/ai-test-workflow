from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from game_agent.exceptions import DeployPhaseError
from game_agent.models.deploy_recovery import DeployRecoveryPatch
from game_agent.models.gameturbo_config import GameTurboConfigPatch
from game_agent.models.settings import AppConfig
from game_agent.modules.retry.analysis import AnalysisAgent
from game_agent.services.deploy_runner import DeployResult, run_deploy
from game_agent.services.pipeline_trace import trace_operation
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.utils.apk_util import update_settings_yaml_from_apk
from game_agent.utils.gameturbo_bootstrap import output_apk_path
from game_agent.utils.gameturbo_config_apply import (
    ConfigApplyResult,
    apply_gameturbo_config_patch,
)

logger = logging.getLogger(__name__)


def _read_deploy_log_tail(log_path: Path | None, *, limit: int = 24_000) -> str:
    if log_path is None or not log_path.is_file():
        return "(deploy.log 不存在或尚未写入)"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(读取 deploy.log 失败: {e})"
    if len(text) <= limit:
        return text
    return text[-limit:]


def apply_deploy_recovery_patch(
    config_path: Path,
    patch: DeployRecoveryPatch,
    *,
    expected_gid: str,
) -> ConfigApplyResult:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    summary: list[str] = []
    changed = False

    gid_fix = (patch.game_id or "").strip() or expected_gid.strip()
    if gid_fix and str(data.get("game_id", "")).strip() != gid_fix:
        data["game_id"] = gid_fix
        changed = True
        summary.append(f"game_id -> {gid_fix}")

    if changed:
        config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8",
        )

    if not patch.retry_only and (patch.direct_patterns or patch.port_rules):
        gt = GameTurboConfigPatch(
            analysis=patch.analysis,
            direct_patterns=patch.direct_patterns,
            port_rules=patch.port_rules,
        )
        sub = apply_gameturbo_config_patch(config_path, gt)
        return ConfigApplyResult(
            path=config_path,
            changed=changed or sub.changed,
            summary=summary + sub.summary,
        )

    return ConfigApplyResult(path=config_path, changed=changed, summary=summary)


async def run_deploy_with_ai_retry(
    app_config: AppConfig,
    *,
    gid: str,
    game_config_path: Path,
    settings_path: Path,
    artifact_root: Path | None,
    audit: RunAuditLogger | None = None,
    phase: str = "init",
) -> DeployResult:
    """
    执行 deploy.sh；失败时读 deploy.log → AI 恢复建议 → 改配置或仅重试，直至成功或耗尽次数。
    """
    max_attempts = app_config.gameturbo.deploy_max_ai_retries
    llm = app_config.llm_multimodal or app_config.llm
    analysis = AnalysisAgent(llm, deepseek=app_config.deepseek)

    last_error = ""
    last_log_path: Path | None = None

    for attempt in range(1, max_attempts + 1):
        log_name = "deploy.log" if attempt == 1 else f"deploy_attempt_{attempt}.log"
        attempt_log = artifact_root / log_name if artifact_root else None

        if audit is not None:
            audit.log_phase(
                phase,
                f"deploy 尝试 {attempt}/{max_attempts}",
                gid=gid,
                log_path=str(attempt_log) if attempt_log else None,
            )

        with trace_operation("deploy", "attempt", attempt=attempt, max=max_attempts) as rec:
            try:
                result = run_deploy(
                    gid,
                    serial=app_config.adb.serial,
                    artifact_root=artifact_root,
                    log_filename=log_name,
                    timeout_s=app_config.gameturbo.deploy_timeout_s,
                )
                rec.ok(returncode=0, attempt=attempt)
                if audit is not None:
                    audit.log_phase(
                        phase,
                        "deploy 成功",
                        gid=gid,
                        attempt=attempt,
                        log_path=str(result.log_path or ""),
                    )
                update_settings_yaml_from_apk(settings_path, output_apk_path())
                return result
            except RuntimeError as e:
                last_error = str(e)
                last_log_path = attempt_log or (
                    artifact_root / "deploy.log" if artifact_root else None
                )
                rec.fail(error=last_error[:500], attempt=attempt)

        if attempt >= max_attempts:
            break

        log_tail = _read_deploy_log_tail(last_log_path)
        try:
            current = json.loads(game_config_path.read_text(encoding="utf-8"))
        except Exception as read_err:
            current = {"_read_error": str(read_err)}

        logger.warning(
            "[DeployRetry] 第 %d 次 deploy 失败，AI 分析中: %s",
            attempt,
            last_error[:200],
        )
        patch = await analysis.analyze_deploy_failure(
            gid=gid,
            attempt=attempt,
            max_attempts=max_attempts,
            deploy_log_tail=log_tail,
            current_config=current,
            last_error=last_error,
        )
        if audit is not None:
            audit.log_phase(
                phase,
                "deploy AI 恢复建议",
                attempt=attempt,
                retry_only=patch.retry_only,
                analysis=patch.analysis[:2000],
            )
        if artifact_root is not None:
            (artifact_root / f"deploy_recovery_{attempt}.json").write_text(
                patch.model_dump_json(indent=2),
                encoding="utf-8",
            )

        if not patch.retry_only:
            apply_result = apply_deploy_recovery_patch(
                game_config_path,
                patch,
                expected_gid=gid,
            )
            logger.info(
                "[DeployRetry] 已应用恢复补丁 changed=%s summary=%s",
                apply_result.changed,
                apply_result.summary,
            )
            if audit is not None:
                audit.log_phase(
                    phase,
                    "deploy 恢复补丁已应用",
                    changed=apply_result.changed,
                    summary=apply_result.summary,
                )
        else:
            logger.info("[DeployRetry] AI 建议仅重试 deploy，不改配置")

        await asyncio.sleep(1.0)

    msg = (
        f"deploy.sh 在 {max_attempts} 次尝试后仍失败 (gid={gid})。"
        f"最后错误: {last_error[:1500]}"
    )
    raise DeployPhaseError(msg, log_path=last_log_path, attempts=max_attempts)


def run_deploy_with_ai_retry_sync(
    app_config: AppConfig,
    settings_path: Path,
    *,
    gid: str,
    game_config_path: Path,
    artifact_root: Path | None,
    audit: RunAuditLogger | None = None,
    phase: str = "init",
) -> DeployResult:
    return asyncio.run(
        run_deploy_with_ai_retry(
            app_config,
            gid=gid,
            game_config_path=game_config_path,
            settings_path=settings_path,
            artifact_root=artifact_root,
            audit=audit,
            phase=phase,
        ),
    )
