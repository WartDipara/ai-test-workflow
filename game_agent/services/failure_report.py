from __future__ import annotations

import logging
from pathlib import Path

from game_agent.models.failure_report import (
    AttemptFailureInsight,
    AttemptRoundDiagnosis,
    FailureDiagnosisReport,
)
from game_agent.models.settings import AppConfig

logger = logging.getLogger(__name__)

ATTEMPT_FAILURE_REPORT_MD = "attempt_failure_report.md"
ATTEMPT_FAILURE_REPORT_JSON = "attempt_failure_report.json"


def user_interrupt_diagnosis_report() -> FailureDiagnosisReport:
    """Static failure report on user interrupt; no LLM call."""
    from game_agent.models.run_failure import USER_INTERRUPT_MESSAGE

    return FailureDiagnosisReport(
        executive_summary=USER_INTERRUPT_MESSAGE,
        overall_verdict=USER_INTERRUPT_MESSAGE,
        confidence="high",
        human_triage_steps=["User interrupted the batch run or task, no AI troubleshooting required"],
        non_config_issues=[USER_INTERRUPT_MESSAGE],
    )


def _guess_failure_stage(reason: str) -> str:
    lower = reason.lower()
    if "preprocess" in lower or "deploy" in lower or "bootstrap" in lower:
        return "init"
    if "log anomaly" in lower or "tunnel closed" in lower or "channel closed" in lower:
        return "observer"
    if "screen anomaly" in lower or "network" in lower and "popup" in lower:
        return "observer"
    if "executor" in lower or "in-game" in lower or "check_in_game" in lower:
        return "executor"
    if "screen" in lower or "observer" in lower or "parallel game" in lower:
        return "observer"
    if "retry" in lower or "config" in lower or "modify" in lower:
        return "modify"
    return "unknown"


async def generate_and_save_attempt_failure_report(
    cfg: AppConfig,
    *,
    retry_no: int,
    artifact_root: Path,
    reason: str,
    gid: str,
    will_retry: bool,
    game_config_path: Path | None,
) -> Path | None:
    from game_agent.external_services.manager import ExternalServiceManager

    mgr = ExternalServiceManager(cfg)
    if mgr.gameturbo_enabled():
        from game_agent.external_services.gameturbo.failure_report import (
            generate_and_save_attempt_failure_report as gt_save,
        )

        return await gt_save(
            cfg,
            retry_no=retry_no,
            artifact_root=artifact_root,
            reason=reason,
            gid=gid,
            will_retry=will_retry,
            game_config_path=game_config_path,
        )

    if not artifact_root.is_dir():
        return None
    report = await generate_attempt_failure_report(
        cfg,
        retry_no=retry_no,
        artifact_root=artifact_root,
        reason=reason,
        gid=gid,
        will_retry=will_retry,
        game_config_path=game_config_path,
    )
    md_path = artifact_root / ATTEMPT_FAILURE_REPORT_MD
    json_path = artifact_root / ATTEMPT_FAILURE_REPORT_JSON
    md_path.write_text(
        report.to_markdown(
            attempt=retry_no,
            gid=gid,
            reason=reason,
            will_retry=will_retry,
        ),
        encoding="utf-8",
    )
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    logger.info("Attempt failure report written: %s", md_path)
    return md_path


async def generate_attempt_failure_report(
    cfg: AppConfig,
    *,
    retry_no: int,
    artifact_root: Path,
    reason: str,
    gid: str,
    will_retry: bool,
    game_config_path: Path | None,
) -> AttemptRoundDiagnosis:
    from game_agent.external_services.manager import ExternalServiceManager

    if ExternalServiceManager(cfg).gameturbo_enabled():
        from game_agent.external_services.gameturbo.failure_report import (
            generate_attempt_failure_report as gt_generate,
        )

        return await gt_generate(
            cfg,
            retry_no=retry_no,
            artifact_root=artifact_root,
            reason=reason,
            gid=gid,
            will_retry=will_retry,
            game_config_path=game_config_path,
        )

    return AttemptRoundDiagnosis(
        round_summary=f"Attempt {retry_no} failed: {reason[:500]}",
        failure_stage=_guess_failure_stage(reason),
        immediate_verdict=reason[:500],
        confidence="low",
        human_triage_steps=[
            "Review process.log and audit/events.jsonl in the artifact directory",
            "Review monitor_screen_*.png for UI stage",
        ],
    )


async def generate_failure_diagnosis_report(
    cfg: AppConfig,
    *,
    gid: str,
    task_id: str,
    last_reason: str,
    attempt_records: list[tuple[int, Path]],
    game_config_path: Path | None,
) -> FailureDiagnosisReport:
    from game_agent.external_services.manager import ExternalServiceManager

    if ExternalServiceManager(cfg).gameturbo_enabled():
        from game_agent.external_services.gameturbo.failure_report import (
            generate_failure_diagnosis_report as gt_generate,
        )

        return await gt_generate(
            cfg,
            gid=gid,
            task_id=task_id,
            last_reason=last_reason,
            attempt_records=attempt_records,
            game_config_path=game_config_path,
        )

    return FailureDiagnosisReport(
        executive_summary=last_reason[:500] or "Task failed without GameTurbo plugin analysis.",
        overall_verdict=last_reason[:500],
        confidence="low",
        attempts=[
            AttemptFailureInsight(
                attempt=retry_no,
                failure_stage=_guess_failure_stage(
                    last_reason if retry_no == attempt_records[-1][0] else "",
                ),
                immediate_trigger=last_reason[:2000]
                if retry_no == attempt_records[-1][0]
                else "",
            )
            for retry_no, _ in attempt_records
        ],
        human_triage_steps=[
            "Review process.log and audit/ per attempt under run_outputs/attempts/",
            "Review monitor_screen_*.png for UI stage",
        ],
    )
