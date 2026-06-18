from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AttemptFailureInsight(BaseModel):
    attempt: int = Field(..., description="第几次尝试，从 1 开始")
    failure_stage: str = Field("", description="init / executor / observer / modify / unknown")
    immediate_trigger: str = Field("", description="编排器记录的即时失败原因")
    log_highlights: list[str] = Field(default_factory=list, description="GameTurbo 日志关键行")
    screen_summary: str = Field("", description="画面/OCR/多模态观察摘要")
    domain_summary: str = Field("", description="域名/区域分析要点")


class FailureDiagnosisReport(BaseModel):
    """供人工排障使用的 AI 失败诊断报告（结构化）。"""

    executive_summary: str = Field("", description="给人工的一页纸结论")
    overall_verdict: str = Field("", description="最可能的失败类型/根因归类")
    confidence: Literal["high", "medium", "low"] = "medium"
    attempts: list[AttemptFailureInsight] = Field(default_factory=list)
    external_log_analysis: str = Field("", description="外部插件日志分析")
    gameturbo_log_analysis: str = Field(
        "",
        description="兼容字段，与 external_log_analysis 同步",
    )
    domain_and_routing_analysis: str = Field("", description="tunnel/direct/区域/ pending IP 等")
    screen_and_game_flow_analysis: str = Field("", description="黑屏、登录、超时、弹窗等")
    config_assessment: str = Field("", description="当前游戏 JSON 配置可能的问题")
    human_triage_steps: list[str] = Field(
        default_factory=list,
        description="建议人工按顺序排查的步骤",
    )
    suggested_config_changes: list[str] = Field(
        default_factory=list,
        description="建议修改 gameturbo JSON 的项（最终失败时供人工改，不自动 deploy）",
    )
    non_config_issues: list[str] = Field(
        default_factory=list,
        description="非配置问题（脚本、设备、游戏本身等）",
    )
    evidence_gaps: list[str] = Field(
        default_factory=list,
        description="证据不足、需补采的信息",
    )

    @model_validator(mode="before")
    @classmethod
    def _sync_external_log_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        ext = data.get("external_log_analysis")
        legacy = data.get("gameturbo_log_analysis")
        if ext and not legacy:
            data["gameturbo_log_analysis"] = ext
        elif legacy and not ext:
            data["external_log_analysis"] = legacy
        return data

    @model_validator(mode="after")
    def _mirror_external_log_fields(self) -> FailureDiagnosisReport:
        if self.external_log_analysis and not self.gameturbo_log_analysis:
            self.gameturbo_log_analysis = self.external_log_analysis
        elif self.gameturbo_log_analysis and not self.external_log_analysis:
            self.external_log_analysis = self.gameturbo_log_analysis
        return self

    def to_markdown(self, *, gid: str, task_id: str, last_reason: str) -> str:
        lines = [
            "# GameTurbo Automated Test Failure Report (AI)",
            "",
            f"- **gid**: {gid}",
            f"- **task_id**: {task_id}",
            f"- **confidence**: {self.confidence}",
            f"- **orchestrator last reason**: {last_reason}",
            "",
            "## Executive summary",
            "",
            self.executive_summary or "(none)",
            "",
            "## Overall verdict",
            "",
            self.overall_verdict or "(none)",
            "",
            "## Per-attempt summary",
            "",
        ]
        if not self.attempts:
            lines.append("(no per-attempt data)\n")
        for item in self.attempts:
            lines.extend(
                [
                    f"### Attempt {item.attempt}",
                    "",
                    f"- **stage**: {item.failure_stage or 'unknown'}",
                    f"- **trigger**: {item.immediate_trigger or '(none)'}",
                    "",
                    "**Log highlights**",
                    "",
                ],
            )
            if item.log_highlights:
                lines.extend(f"- {line}" for line in item.log_highlights)
            else:
                lines.append("- (none)")
            lines.extend(
                [
                    "",
                    f"**Screen**: {item.screen_summary or '(none)'}",
                    "",
                    f"**Domain/routing**: {item.domain_summary or '(none)'}",
                    "",
                ],
            )

        lines.extend(
            [
                "## External log analysis",
                "",
                self.external_log_analysis or self.gameturbo_log_analysis or "(none)",
                "",
                "## Domain and routing",
                "",
                self.domain_and_routing_analysis or "(none)",
                "",
                "## Screen and game flow",
                "",
                self.screen_and_game_flow_analysis or "(none)",
                "",
                "## Config assessment",
                "",
                self.config_assessment or "(none)",
                "",
                "## Recommended manual triage steps",
                "",
            ],
        )
        if self.human_triage_steps:
            lines.extend(f"{i}. {step}" for i, step in enumerate(self.human_triage_steps, 1))
        else:
            lines.append("(none)")

        lines.extend(["", "## Suggested config changes (manual)", ""])
        if self.suggested_config_changes:
            lines.extend(f"- {item}" for item in self.suggested_config_changes)
        else:
            lines.append("- (none)")

        lines.extend(["", "## Non-config issues", ""])
        if self.non_config_issues:
            lines.extend(f"- {item}" for item in self.non_config_issues)
        else:
            lines.append("- (none)")

        lines.extend(["", "## Evidence gaps", ""])
        if self.evidence_gaps:
            lines.extend(f"- {item}" for item in self.evidence_gaps)
        else:
            lines.append("- (none)")

        return "\n".join(lines) + "\n"


class AttemptRoundDiagnosis(BaseModel):
    """单轮失败时的 AI 诊断（写入 artifacts/retry_*/attempt_failure_report.md）。"""

    round_summary: str = Field("", description="本轮 2-3 句结论")
    failure_stage: str = Field("", description="init / executor / observer / modify / unknown")
    immediate_verdict: str = Field("", description="本轮最可能原因归类")
    confidence: Literal["high", "medium", "low"] = "medium"
    log_highlights: list[str] = Field(default_factory=list, description="3-8 条关键日志原文")
    external_log_analysis: str = Field("", description="本轮外部插件日志分析")
    gameturbo_log_analysis: str = Field(
        "",
        description="兼容字段，与 external_log_analysis 同步",
    )
    domain_and_routing_analysis: str = Field("", description="域名/隧道/直连/区域")
    screen_and_game_flow_analysis: str = Field("", description="画面与流程")
    config_assessment: str = Field("", description="配置问题评估")
    modify_stage_notes: str = Field(
        "",
        description="若已执行 Modify/deploy，说明补丁意图与是否可能改善",
    )
    human_triage_steps: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(
        default_factory=list,
        description="下一轮重试前或人工介入建议",
    )
    evidence_gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _sync_round_external_log_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        ext = data.get("external_log_analysis")
        legacy = data.get("gameturbo_log_analysis")
        if ext and not legacy:
            data["gameturbo_log_analysis"] = ext
        elif legacy and not ext:
            data["external_log_analysis"] = legacy
        return data

    @model_validator(mode="after")
    def _mirror_round_external_log_fields(self) -> AttemptRoundDiagnosis:
        if self.external_log_analysis and not self.gameturbo_log_analysis:
            self.gameturbo_log_analysis = self.external_log_analysis
        elif self.gameturbo_log_analysis and not self.external_log_analysis:
            self.external_log_analysis = self.gameturbo_log_analysis
        return self

    def to_markdown(
        self,
        *,
        attempt: int,
        gid: str,
        reason: str,
        will_retry: bool,
    ) -> str:
        next_hint = (
            "Orchestrator will auto-retry after this round"
            if will_retry
            else "Final attempt or retries disabled"
        )
        lines = [
            "# Attempt Failure Report (AI)",
            "",
            f"- **attempt**: {attempt}",
            f"- **gid**: {gid}",
            f"- **trigger reason**: {reason}",
            f"- **next**: {next_hint}",
            f"- **confidence**: {self.confidence}",
            f"- **stage**: {self.failure_stage or 'unknown'}",
            "",
            "## Round summary",
            "",
            self.round_summary or "(none)",
            "",
            "## Verdict",
            "",
            self.immediate_verdict or "(none)",
            "",
            "## Log highlights",
            "",
        ]
        if self.log_highlights:
            lines.extend(f"- {line}" for line in self.log_highlights)
        else:
            lines.append("- (none)")

        lines.extend(
            [
                "",
                "## External log",
                "",
                self.external_log_analysis or self.gameturbo_log_analysis or "(none)",
                "",
                "## Domain and routing",
                "",
                self.domain_and_routing_analysis or "(none)",
                "",
                "## Screen and flow",
                "",
                self.screen_and_game_flow_analysis or "(none)",
                "",
                "## Config assessment",
                "",
                self.config_assessment or "(none)",
                "",
                "## Modify stage notes",
                "",
                self.modify_stage_notes or "(not run or no patch written)",
                "",
                "## Suggested actions",
                "",
            ],
        )
        if self.suggested_actions:
            lines.extend(f"- {item}" for item in self.suggested_actions)
        else:
            lines.append("- (none)")

        lines.extend(["", "## Manual triage steps", ""])
        if self.human_triage_steps:
            lines.extend(f"{i}. {step}" for i, step in enumerate(self.human_triage_steps, 1))
        else:
            lines.append("(none)")

        lines.extend(["", "## Evidence gaps", ""])
        if self.evidence_gaps:
            lines.extend(f"- {item}" for item in self.evidence_gaps)
        else:
            lines.append("(none)")

        return "\n".join(lines) + "\n"
