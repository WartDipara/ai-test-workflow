from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic_ai.messages import BinaryImage

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
    keywizard_dir = artifact_root / "keywizard"
    keywizard_shots = (
        sorted(keywizard_dir.glob("**/*.png")) if keywizard_dir.is_dir() else []
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
        "keywizard_screenshot_paths": [str(p) for p in keywizard_shots[-2:]],
    }


def _guess_failure_stage(reason: str) -> str:
    lower = reason.lower()
    if "前置处理" in reason or "deploy" in lower or "bootstrap" in lower:
        return "init"
    if "按键精灵" in reason or "keywizard" in lower or "游戏进程" in reason:
        return "keywizard"
    if "screen" in lower or "画面" in reason or "observer" in lower or "log anomaly" in lower:
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
    config_block = "（无）"
    if game_config_path and game_config_path.is_file():
        config_block = _read_text(game_config_path, limit=12000)

    retry_note = (
        "本轮结束后编排器将自动重试；请评估已应用的配置补丁是否可能解决根因，并在 suggested_actions 中说明。"
        if will_retry
        else "本轮为最后一次尝试或已关闭 retry_on_failure；建议侧重人工修复项。"
    )

    prompt = f"""
{gameturbo_log_baseline_prompt_block()}

你是 GameTurbo 排障专家。自动化测试**第 {retry_no} 轮**失败，请输出单轮诊断 AttemptRoundDiagnosis。
{retry_note}
判定日志时须对照基线，区分正常噪声与真实故障。

gid: {gid}
编排器记录原因: {reason}
failure_stage 请从 init/keywizard/observer/modify/unknown 中选最贴切的一项。

当前游戏配置 JSON:
{config_block}

本轮证据包:
{json.dumps(bundle, ensure_ascii=False, indent=2)}

要求:
1. round_summary / immediate_verdict 必须具体，禁止空话
2. log_highlights: 3-8 条，尽量保留日志原文关键片段
3. 结合 domain_analysis、截图路径、ai_patch_report（若有）分析
4. modify_stage_notes: 若 ai_patch_report 非空，说明补丁做了什么、是否值得下一轮验证
5. suggested_actions: 面向下一轮或人工的具体动作
6. evidence_gaps: 本轮仍缺什么证据

只分析**本轮**，不要写「所有轮次总结」。
"""

    llm_cfg = cfg.llm_multimodal or cfg.llm
    agent = AnalysisAgent(llm_cfg)

    screenshots = sorted(artifact_root.glob("monitor_screen_*.png"))[-_MAX_SCREENSHOTS_ATTEMPT:]
    messages: list = [prompt]
    for shot in screenshots:
        if shot.is_file():
            messages.append(BinaryImage.from_path(shot))

    try:
        report = await agent.generate_attempt_round_diagnosis(messages)
        if not report.failure_stage:
            report.failure_stage = _guess_failure_stage(reason)
        return report
    except Exception as e:
        logger.error("生成本轮 AI 失败报告异常: %s", e)
        return AttemptRoundDiagnosis(
            round_summary=f"AI 本轮报告生成失败: {e}",
            failure_stage=_guess_failure_stage(reason),
            immediate_verdict=reason[:500],
            confidence="low",
            gameturbo_log_analysis="请查看本目录 gameturbo.log",
            human_triage_steps=["查看 gameturbo.log 与 domain_region_analysis.json"],
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

    config_block = "（无游戏配置路径）"
    if game_config_path and game_config_path.is_file():
        config_block = _read_text(game_config_path, limit=12000)

    prompt = f"""
{gameturbo_log_baseline_prompt_block()}

你是资深 GameTurbo/Android 网络加速排障专家。自动化测试任务最终失败，需要输出**给人工作排查**的高质量诊断报告。
禁止只复述「发生了异常」；必须基于日志、域名 JSON、截图、审计轨迹给出可执行结论。
禁止将正常基线中的 heartbeat 重连、recv buffer full、FEC 等单独判为失败根因。

任务信息:
- gid: {gid}
- task_id: {task_id}
- 编排器最后失败原因: {last_reason}

当前游戏配置 JSON（games/ 下，供评估，最终失败不会自动交付此文件）:
{config_block}

各轮证据包（JSON，含每轮 attempt_failure_report 摘要、日志尾部、域名分析、patch 报告、process/deploy、审计摘要）:
{json.dumps(bundles, ensure_ascii=False, indent=2)}

请输出 FailureDiagnosisReport 结构：
1. executive_summary：2-4 句，人工先读这个就能行动
2. overall_verdict：一句话归类（配置/路由/游戏画面/按键精灵/部署设备等）
3. confidence：high/medium/low
4. attempts：每轮 failure_stage、immediate_trigger、log_highlights（3-8 条原文级关键行）、screen_summary、domain_summary
5. gameturbo_log_analysis：综合所有轮次日志，指出 tunnel/direct/RTT/closed/rebuilt 等
6. domain_and_routing_analysis：结合 domain_analysis JSON
7. screen_and_game_flow_analysis：结合截图路径所描述的现象（黑屏、登录卡住、超时文案等）
8. config_assessment：当前 JSON 哪些字段可能不对
9. human_triage_steps：有序步骤，便于人工复现与验证
10. suggested_config_changes：供人工改 direct_patterns 或 port_rules（勿建议改 default_action；慎增 direct）
11. non_config_issues：若非配置问题，写清脚本/设备/游戏侧
12. evidence_gaps：还缺什么日志/截图才能定论

注意：这是**最终失败报告**，侧重帮助人工修复；不要输出「下次自动重试会自动好」这类空话。
"""

    llm_cfg = cfg.llm_multimodal or cfg.llm
    agent = AnalysisAgent(llm_cfg)

    screenshots: list[Path] = []
    for _, artifact_root in attempt_records:
        screenshots.extend(sorted(artifact_root.glob("monitor_screen_*.png")))
    screenshots = screenshots[-_MAX_SCREENSHOTS_TOTAL:]

    messages: list = [prompt]
    for shot in screenshots:
        if shot.is_file():
            messages.append(BinaryImage.from_path(shot))

    try:
        report = await agent.generate_failure_diagnosis(messages)
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
            executive_summary=f"AI 报告生成失败: {e}",
            overall_verdict=last_reason[:500],
            confidence="low",
            gameturbo_log_analysis="请直接查看 attempts/ 下各轮 gameturbo.log",
            human_triage_steps=[
                "查看 run_outputs 下 attempts 各轮的 gameturbo.log 与 audit/ai_trace.md",
                "对照 domain_region_analysis.json 检查 tunnel/direct 域名",
                "查看 monitor_screen_*.png 确认画面阶段",
            ],
            evidence_gaps=[f"AI 报告生成失败: {e}"],
        )
