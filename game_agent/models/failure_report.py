from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    gameturbo_log_analysis: str = Field("", description="结合完整/长日志的分析")
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

    def to_markdown(self, *, gid: str, task_id: str, last_reason: str) -> str:
        lines = [
            "# GameTurbo 自动化测试失败诊断报告（AI）",
            "",
            f"- **gid**: {gid}",
            f"- **task_id**: {task_id}",
            f"- **置信度**: {self.confidence}",
            f"- **编排器最后记录**: {last_reason}",
            "",
            "## 执行摘要",
            "",
            self.executive_summary or "（无）",
            "",
            "## 总体判断",
            "",
            self.overall_verdict or "（无）",
            "",
            "## 分轮次情况",
            "",
        ]
        if not self.attempts:
            lines.append("（无分轮数据）\n")
        for item in self.attempts:
            lines.extend(
                [
                    f"### 第 {item.attempt} 轮",
                    "",
                    f"- **阶段**: {item.failure_stage or 'unknown'}",
                    f"- **触发原因**: {item.immediate_trigger or '（无）'}",
                    "",
                    "**日志要点**",
                    "",
                ],
            )
            if item.log_highlights:
                lines.extend(f"- {line}" for line in item.log_highlights)
            else:
                lines.append("- （无）")
            lines.extend(
                [
                    "",
                    f"**画面**: {item.screen_summary or '（无）'}",
                    "",
                    f"**域名/路由**: {item.domain_summary or '（无）'}",
                    "",
                ],
            )

        lines.extend(
            [
                "## GameTurbo 日志分析",
                "",
                self.gameturbo_log_analysis or "（无）",
                "",
                "## 域名与路由",
                "",
                self.domain_and_routing_analysis or "（无）",
                "",
                "## 画面与游戏流程",
                "",
                self.screen_and_game_flow_analysis or "（无）",
                "",
                "## 配置评估",
                "",
                self.config_assessment or "（无）",
                "",
                "## 建议人工排查步骤",
                "",
            ],
        )
        if self.human_triage_steps:
            lines.extend(f"{i}. {step}" for i, step in enumerate(self.human_triage_steps, 1))
        else:
            lines.append("（无）")

        lines.extend(["", "## 建议配置修改（供人工）", ""])
        if self.suggested_config_changes:
            lines.extend(f"- {item}" for item in self.suggested_config_changes)
        else:
            lines.append("- （无）")

        lines.extend(["", "## 非配置类问题", ""])
        if self.non_config_issues:
            lines.extend(f"- {item}" for item in self.non_config_issues)
        else:
            lines.append("- （无）")

        lines.extend(["", "## 证据缺口", ""])
        if self.evidence_gaps:
            lines.extend(f"- {item}" for item in self.evidence_gaps)
        else:
            lines.append("- （无）")

        return "\n".join(lines) + "\n"


class AttemptRoundDiagnosis(BaseModel):
    """单轮失败时的 AI 诊断（写入 artifacts/retry_*/attempt_failure_report.md）。"""

    round_summary: str = Field("", description="本轮 2-3 句结论")
    failure_stage: str = Field("", description="init / executor / observer / modify / unknown")
    immediate_verdict: str = Field("", description="本轮最可能原因归类")
    confidence: Literal["high", "medium", "low"] = "medium"
    log_highlights: list[str] = Field(default_factory=list, description="3-8 条关键日志原文")
    gameturbo_log_analysis: str = Field("", description="本轮 GameTurbo 日志分析")
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

    def to_markdown(
        self,
        *,
        attempt: int,
        gid: str,
        reason: str,
        will_retry: bool,
    ) -> str:
        next_hint = "编排器将自动进入下一轮重试" if will_retry else "此为最后一轮或已关闭重试"
        lines = [
            "# 本轮失败诊断报告（AI）",
            "",
            f"- **第 {attempt} 轮**",
            f"- **gid**: {gid}",
            f"- **触发原因**: {reason}",
            f"- **后续**: {next_hint}",
            f"- **置信度**: {self.confidence}",
            f"- **阶段**: {self.failure_stage or 'unknown'}",
            "",
            "## 本轮摘要",
            "",
            self.round_summary or "（无）",
            "",
            "## 判断",
            "",
            self.immediate_verdict or "（无）",
            "",
            "## 日志要点",
            "",
        ]
        if self.log_highlights:
            lines.extend(f"- {line}" for line in self.log_highlights)
        else:
            lines.append("- （无）")

        lines.extend(
            [
                "",
                "## GameTurbo 日志",
                "",
                self.gameturbo_log_analysis or "（无）",
                "",
                "## 域名与路由",
                "",
                self.domain_and_routing_analysis or "（无）",
                "",
                "## 画面与流程",
                "",
                self.screen_and_game_flow_analysis or "（无）",
                "",
                "## 配置评估",
                "",
                self.config_assessment or "（无）",
                "",
                "## Modify 阶段说明",
                "",
                self.modify_stage_notes or "（未执行或未写入补丁）",
                "",
                "## 建议操作",
                "",
            ],
        )
        if self.suggested_actions:
            lines.extend(f"- {item}" for item in self.suggested_actions)
        else:
            lines.append("- （无）")

        lines.extend(["", "## 人工排查步骤", ""])
        if self.human_triage_steps:
            lines.extend(f"{i}. {step}" for i, step in enumerate(self.human_triage_steps, 1))
        else:
            lines.append("（无）")

        lines.extend(["", "## 证据缺口", ""])
        if self.evidence_gaps:
            lines.extend(f"- {item}" for item in self.evidence_gaps)
        else:
            lines.append("- （无）")

        return "\n".join(lines) + "\n"
