from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from game_agent.models.failure_report import (
    AttemptFailureInsight,
    AttemptRoundDiagnosis,
    FailureDiagnosisReport,
)

ATTEMPT_FAILURE_REPORT_MD = "attempt_failure_report.md"
ATTEMPT_FAILURE_REPORT_JSON = "attempt_failure_report.json"
from game_agent.models.settings import AppConfig
from game_agent.modules.retry.analysis import AnalysisAgent
from game_agent.utils.gameturbo_log_domain_extract import (
    DEFAULT_OUTPUT_NAME,
    format_domain_analysis_for_ai,
)
from game_agent.utils.gameturbo_log_skill import gameturbo_log_baseline_prompt_block

logger = logging.getLogger(__name__)

_LOG_TAIL_PER_ATTEMPT = 24_000
_MAX_SCREENSHOTS_ATTEMPT = 5
_MAX_SCREENSHOTS_TOTAL = 9


def _read_text(path: Path, *, limit: int | None = None) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if limit is not None and len(text) > limit:
        return text[-limit:]
    return text


def _load_domain_json(artifact_root: Path) -> dict[str, Any] | None:
    path = artifact_root / DEFAULT_OUTPUT_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _collect_attempt_bundle(
    retry_no: int,
    artifact_root: Path,
    *,
    last_reason: str,
) -> dict[str, Any]:
    log_path = artifact_root / "gameturbo.log"
    log_content = _read_text(log_path, limit=_LOG_TAIL_PER_ATTEMPT)
    domain = _load_domain_json(artifact_root)
    domain_for_ai = format_domain_analysis_for_ai(domain) if domain else ""
    patch_report = _read_text(artifact_root / "ai_analysis_report.txt", limit=8000)
    process_tail = _read_text(artifact_root / "process.log", limit=12000)
    deploy_log = _read_text(artifact_root / "deploy.log", limit=8000)

    audit_trace = artifact_root / "audit" / "ai_trace.md"
    audit_events = artifact_root / "audit" / "events.jsonl"
    transcripts = sorted(artifact_root.glob("audit/round_*_transcript.txt"))

    screenshots = sorted(artifact_root.glob("monitor_screen_*.png"))
    executor_dir = artifact_root / "executor"
    executor_shots = (
        sorted(executor_dir.glob("**/*.png")) if executor_dir.is_dir() else []
    )

    attempt_report_md = _read_text(
        artifact_root / ATTEMPT_FAILURE_REPORT_MD,
        limit=8000,
    )

    return {
        "attempt": retry_no,
        "artifact_root": str(artifact_root),
        "immediate_reason": last_reason,
        "attempt_failure_report_excerpt": attempt_report_md,
        "gameturbo_log_chars": len(log_content),
        "gameturbo_log_tail": log_content,
        "domain_analysis": domain,
        "domain_analysis_for_ai": domain_for_ai,
        "ai_patch_report": patch_report,
        "process_log_tail": process_tail,
        "deploy_log_tail": deploy_log,
        "audit_trace_excerpt": _read_text(audit_trace, limit=12000),
        "audit_events_tail": _read_text(audit_events, limit=12000),
        "transcript_files": [str(p) for p in transcripts[-2:]],
        "transcript_excerpts": [_read_text(p, limit=6000) for p in transcripts[-2:]],
        "screenshot_paths": [str(p) for p in screenshots[-3:]],
        "executor_screenshot_paths": [str(p) for p in executor_shots[-2:]],
    }


def _guess_failure_stage(reason: str) -> str:
    lower = reason.lower()
    if "前置处理" in reason or "deploy" in lower or "bootstrap" in lower:
        return "init"
    if "log anomaly" in lower or "tunnel closed" in lower or "channel closed" in lower:
        return "observer"
    if "screen anomaly" in lower or "network" in lower and "popup" in lower:
        return "observer"
    if "执行者" in reason or "executor" in lower or "in-game" in lower or "check_in_game" in lower:
        return "executor"
    if "screen" in lower or "画面" in reason or "observer" in lower or "parallel game" in lower:
        return "observer"
    if "retry" in lower or "配置" in reason:
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
    """在本轮 artifact 目录写入 attempt_failure_report.md / .json。"""
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
    json_path.write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("本轮 AI 失败报告已写入: %s", md_path)
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
    bundle = _collect_attempt_bundle(retry_no, artifact_root, last_reason=reason)
    config_block = "(none)"
    if game_config_path and game_config_path.is_file():
        config_block = _read_text(game_config_path, limit=12000)

    retry_note = (
        "Orchestrator will auto-retry after this round; assess whether applied config patch may fix root cause; state in suggested_actions."
        if will_retry
        else "Last attempt or retry_on_failure disabled; focus on manual remediation."
    )

    prompt = f"""
{gameturbo_log_baseline_prompt_block()}

You are a GameTurbo triage expert. Automated test **attempt {retry_no}** failed — output AttemptRoundDiagnosis for this round only.
{retry_note}
Judge logs against the baseline; separate benign noise from real faults.

gid: {gid}
Orchestrator reason: {reason}
Pick failure_stage: init | executor | observer | modify | unknown.

Current game config JSON:
{config_block}

Evidence bundle this round:
{json.dumps(bundle, ensure_ascii=False, indent=2)}

Requirements:
1. round_summary / immediate_verdict must be specific — no filler
2. log_highlights: 3–8 items with key log excerpts
3. Use domain_analysis, screenshot paths, ai_patch_report if present
4. modify_stage_notes: if ai_patch_report non-empty, what changed and worth verifying next round
5. suggested_actions: concrete next steps for retry or humans
6. evidence_gaps: what is still missing

Analyze **this attempt only** — not a multi-attempt executive summary.
"""

    agent = AnalysisAgent(cfg.llm, deepseek=cfg.deepseek)

    screenshots = sorted(artifact_root.glob("monitor_screen_*.png"))[-_MAX_SCREENSHOTS_ATTEMPT:]
    if cfg.llm_multimodal is not None and screenshots:
        from game_agent.services.vision_context import summarize_monitor_screenshots

        prompt += "\n\n" + await summarize_monitor_screenshots(
            cfg.llm_multimodal,
            screenshots,
            max_images=_MAX_SCREENSHOTS_ATTEMPT,
        )

    try:
        report = await agent.generate_attempt_round_diagnosis([prompt])
        if not report.failure_stage:
            report.failure_stage = _guess_failure_stage(reason)
        return report
    except Exception as e:
        logger.error("生成本轮 AI 失败报告异常: %s", e)
        return AttemptRoundDiagnosis(
            round_summary=f"AI attempt report failed: {e}",
            failure_stage=_guess_failure_stage(reason),
            immediate_verdict=reason[:500],
            confidence="low",
            gameturbo_log_analysis="See gameturbo.log in this artifact dir",
            human_triage_steps=["Review gameturbo.log and domain_region_analysis.json"],
            evidence_gaps=[str(e)],
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
    """汇总各轮完整证据，由 AI 生成面向人工排障的失败报告。"""
    bundles = [
        _collect_attempt_bundle(
            retry_no,
            artifact_root,
            last_reason=last_reason if retry_no == attempt_records[-1][0] else "",
        )
        for retry_no, artifact_root in attempt_records
    ]

    config_block = "(no game config path)"
    if game_config_path and game_config_path.is_file():
        config_block = _read_text(game_config_path, limit=12000)

    prompt = f"""
{gameturbo_log_baseline_prompt_block()}

You are a senior GameTurbo/Android network-acceleration triage expert. The automated task **failed finally** — produce a high-quality report for **human** investigation.
Do not only restate "an exception occurred"; use logs, domain JSON, screenshots, audit trail for actionable conclusions.
Do not treat baseline heartbeat reconnect, recv buffer full, FEC alone as root cause.

Task:
- gid: {gid}
- task_id: {task_id}
- Orchestrator last failure: {last_reason}

Current game config JSON (under games/; not auto-delivered on final failure):
{config_block}

Per-attempt evidence bundles (JSON):
{json.dumps(bundles, ensure_ascii=False, indent=2)}

Output FailureDiagnosisReport:
1. executive_summary: 2–4 sentences — enough to act
2. overall_verdict: one line (config/routing/UI/executor login/deploy device/…)
3. confidence: high | medium | low
4. attempts: per round failure_stage, immediate_trigger, log_highlights (3–8 lines), screen_summary, domain_summary
5. gameturbo_log_analysis: all rounds — tunnel/direct/RTT/closed/rebuilt
6. domain_and_routing_analysis: from domain JSON
7. screen_and_game_flow_analysis: from screenshot paths (black screen, login stuck, timeout copy, etc.)
8. config_assessment: which JSON fields may be wrong
9. human_triage_steps: ordered reproduction/verification steps
10. suggested_config_changes: manual direct_patterns/port_rules only (not default_action; cautious on direct)
11. non_config_issues: scripts/device/game if not config
12. evidence_gaps: missing logs/screenshots to conclude

Final failure report for humans — no empty "retry will fix it" claims.
"""

    agent = AnalysisAgent(cfg.llm, deepseek=cfg.deepseek)

    screenshots: list[Path] = []
    for _, artifact_root in attempt_records:
        screenshots.extend(sorted(artifact_root.glob("monitor_screen_*.png")))
    screenshots = screenshots[-_MAX_SCREENSHOTS_TOTAL:]

    if cfg.llm_multimodal is not None and screenshots:
        from game_agent.services.vision_context import summarize_monitor_screenshots

        prompt += "\n\n" + await summarize_monitor_screenshots(
            cfg.llm_multimodal,
            screenshots,
            max_images=_MAX_SCREENSHOTS_TOTAL,
        )

    try:
        report = await agent.generate_failure_diagnosis([prompt])
        if not report.attempts:
            report.attempts = [
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
            ]
        return report
    except Exception as e:
        logger.error("生成 AI 失败诊断报告异常: %s", e)
        return FailureDiagnosisReport(
            executive_summary=f"AI report generation failed: {e}",
            overall_verdict=last_reason[:500],
            confidence="low",
            gameturbo_log_analysis="See gameturbo.log under each attempt in attempts/",
            human_triage_steps=[
                "Review gameturbo.log and audit/ai_trace.md per attempt under run_outputs",
                "Compare domain_region_analysis.json for tunnel vs direct domains",
                "Review monitor_screen_*.png for UI stage",
            ],
            evidence_gaps=[f"AI report generation failed: {e}"],
        )
