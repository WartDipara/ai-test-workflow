from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryImage

from game_agent.models.deploy_recovery import DeployRecoveryPatch
from game_agent.models.failure_report import AttemptRoundDiagnosis, FailureDiagnosisReport
from game_agent.models.gameturbo_config import GameTurboConfigPatch
from game_agent.models.settings import LLMSection
from game_agent.services.llm_service import build_llm_model
from game_agent.services.pipeline_trace import trace_operation
from game_agent.utils.gameturbo_log_domain_extract import format_domain_analysis_for_ai
from game_agent.utils.gameturbo_log_skill import gameturbo_log_baseline_prompt_block

logger = logging.getLogger(__name__)

class AnalysisAgent:
    """
    负责在监控抛出异常后，进行二次日志与画面分析，并决定如何重写配置。
    """
    def __init__(self, llm_config: LLMSection) -> None:
        self._llm_config = llm_config
        self._patch_agent = Agent(build_llm_model(llm_config), output_type=GameTurboConfigPatch)
        self._failure_report_agent = Agent(
            build_llm_model(llm_config),
            output_type=FailureDiagnosisReport,
        )
        self._attempt_round_agent = Agent(
            build_llm_model(llm_config),
            output_type=AttemptRoundDiagnosis,
        )
        self._deploy_recovery_agent = Agent(
            build_llm_model(llm_config),
            output_type=DeployRecoveryPatch,
        )

    async def analyze_deploy_failure(
        self,
        *,
        gid: str,
        attempt: int,
        max_attempts: int,
        deploy_log_tail: str,
        current_config: dict[str, Any],
        last_error: str,
    ) -> DeployRecoveryPatch:
        """根据 deploy.log 分析失败原因并给出恢复补丁（可改配置或仅重试）。"""
        config_block = json.dumps(current_config, ensure_ascii=False, indent=2)
        prompt = f"""
你是 GameTurbo Android deploy.sh 故障恢复助手。deploy 打包/注入/安装失败，流程不应直接终止，需要给出可执行的恢复建议。

目标 gid: {gid}
当前为第 {attempt}/{max_attempts} 次 deploy 尝试。
最近一次 Python 侧错误摘要: {last_error[:2000]}

当前游戏配置 JSON（games/gameturbo_{gid}_*.json）:
{config_block}

deploy.sh 日志（末尾截断，含 stdout/stderr）:
{deploy_log_tail[-20000:]}

## 常见失败类型与对策

1. **找不到配置** `No game config found` / `gameturbo_*`：检查 game_id 是否与 gid 一致；JSON 是否合法。
2. **merge_config / Python**：检查 JSON 语法、必填字段；修正后 retry。
3. **build.sh / NDK / 编译**：多为环境或瞬时问题，可 `retry_only=true` 重试；若日志明确缺依赖则写在 analysis。
4. **APK 未找到**：检查 packages 目录原包是否存在，通常无法靠改 games JSON 修复，analysis 说明即可。
5. **adb / 签名 / 安装**：设备或 keystore 问题，retry_only 可试一次。

## 输出 DeployRecoveryPatch

- **analysis**（必填）：根因 + 本次建议动作。
- **retry_only**：true 表示不修改配置，仅立即重试 deploy。
- **game_id**：仅当配置中 game_id 错误时填写正确 gid 字符串。
- **direct_patterns / port_rules**：仅当确信 merge 失败由配置内容引起时少量追加；多数 build 失败应留空。

禁止建议修改 _platform、default_action、tunnel_patterns（deploy 阶段无效）。
"""
        try:
            with trace_operation("llm", "analyze_deploy_failure", attempt=attempt) as rec:
                result = await self._deploy_recovery_agent.run(prompt)
                patch = result.output or DeployRecoveryPatch(
                    analysis="模型未输出 deploy 恢复建议",
                    retry_only=True,
                )
                rec.ok(retry_only=patch.retry_only, game_id=patch.game_id)
                return patch
        except Exception as e:
            logger.error("deploy 失败 AI 分析异常: %s", e)
            return DeployRecoveryPatch(
                analysis=f"AI 分析失败，将直接重试 deploy: {e}",
                retry_only=True,
            )

    async def analyze_and_propose_patch(
        self,
        *,
        anomaly_reason: str,
        log_content: str,
        current_config: dict[str, Any],
        domain_analysis: dict[str, Any] | None = None,
        screenshot_paths: list[Path],
    ) -> GameTurboConfigPatch:
        logger.info("AnalysisAgent 开始生成结构化 GameTurbo 配置补丁...")
        if not domain_analysis:
            return GameTurboConfigPatch(
                analysis=(
                    "缺少 domain_region_analysis.json，无法依据 "
                    "extract_domain_region_from_log.sh / check_target_stability.py 等价结果改配置。"
                    "请确认 gameturbo.log 已导出且域名分析成功。"
                ),
            )

        domain_block = format_domain_analysis_for_ai(domain_analysis)
        current_config_block = json.dumps(current_config, ensure_ascii=False, indent=2)

        domain_priority_note = (
            "【强制】下方域名/区域分析来自 game_agent.utils.gameturbo_log_domain_extract，"
            "与 GameTurbo-Native/extract_domain_region_from_log.sh + check_target_stability.py 对齐。"
            "direct_patterns 只能从 direct_domains 中挑选确信为资源/CDN/渠道/SDK 的项；"
            "unknown_domains 禁止批量 direct；tunnel_domains 禁止改为 direct。"
        )

        prompt = f"""
{gameturbo_log_baseline_prompt_block()}

你是 GameTurbo 网络加速配置修复助手。本项目测试的是**网络加速**能力：只有走 tunnel 的流量才经加速；
若把业务域名大量加入 direct_patterns，游戏虽能联网，但等于未测加速，属于错误方向。

当前处于 Modify 阶段。禁止新建配置；禁止修改 game_id、_platform、default_action、tunnel_patterns。

## 允许修改的字段（仅此二项）

1. **direct_patterns**（多数情况只改此项）：向已有列表**追加**域名或后缀。
2. **port_rules**（少数情况）：按 port 合并规则；元素须含整数 port 与 action 等 deploy 可接受字段。

## direct_patterns 的严格准入（必须同时满足）

仅当**确信**该域名属于以下之一，才可加入 direct：
- 游戏方自有 CDN / 资源分发节点（静态资源、patch、bundle、图片 CDN 等，日志中常为大包下载或 [SNI-DIRECT] 且与玩法无关）；
- 渠道/SDK/统计/崩溃上报等（umeng、bugly、x7 渠道域等），且 domain JSON 中 route 为 direct 或 unknown 但 geo/用途明显非游戏战斗流量；
- 明显下载/更新域名（cdn、download、res、static 等语义，且有 direct 或 unknown 证据）。

**禁止**加入 direct 的情形：
- 游戏战斗/登录/区服/网关/同步等核心业务域（即使暂时 unknown，也应用 tunnel，不要 direct）；
- 仅为「让玩家能连上」而把未知域全部 direct；
- 日志中已是 [SNI-TUNNEL] 且工作正常的域（不要改 direct）；
- 未在域名/区域分析 JSON 中出现、且日志无 [SNI-DIRECT] 佐证的单凭猜测域名。

default_action 在配置中保持 **tunnel** 即可，**不要**在补丁中提出修改。

**不要**使用 tunnel_patterns（不在本阶段补丁模型中）；加速依赖 default_action=tunnel + 少量必要的 direct 放行。

## 决策顺序

1. 精读「域名/区域分析 JSON」：direct_domains、tunnel_domains、unknown_domains、unmatched_pending_ips、non_china_domains。
2. 对照 GameTurbo 日志中的 [SNI-*] / [PENDING-SNI]。
3. 仅对符合「资源/CDN/下载/渠道」准入的项追加到 direct_patterns（去重，勿照抄整表）。
4. 若无合格项，返回空 direct_patterns，在 analysis 说明原因。

{domain_priority_note}

输出 GameTurboConfigPatch：analysis 必填；direct_patterns / port_rules 若无变更则留空列表。
勿因 heartbeat、recv buffer full 等基线噪声改配置。

异常概览:
{anomaly_reason}

当前游戏配置 JSON:
{current_config_block}

域名/区域分析 JSON（Modify 首要依据）:
{domain_block}

GameTurbo 日志片段（末尾截断，辅助）:
{log_content[-16000:]}
"""

        messages = [prompt]
        for sp in screenshot_paths[-3:]:
            if sp.exists():
                messages.append(BinaryImage.from_path(sp))

        try:
            with trace_operation("llm", "analyze_and_propose_patch") as rec:
                result = await self._patch_agent.run(messages)
                patch = result.output or GameTurboConfigPatch(analysis="模型未输出补丁")
                rec.ok(
                    direct_patterns=len(patch.direct_patterns),
                    port_rules=len(patch.port_rules),
                )
                return patch
        except Exception as e:
            logger.error("AnalysisAgent 生成配置补丁失败: %s", e)
            return GameTurboConfigPatch(analysis=f"生成配置补丁失败: {e}")

    async def generate_attempt_round_diagnosis(
        self,
        prompt_messages: list,
    ) -> AttemptRoundDiagnosis:
        logger.info("AnalysisAgent 生成本轮失败诊断报告...")
        try:
            result = await self._attempt_round_agent.run(prompt_messages)
            return result.output or AttemptRoundDiagnosis(
                round_summary="模型未输出结构化报告",
                confidence="low",
            )
        except Exception as e:
            logger.error("AnalysisAgent 本轮失败诊断生成失败: %s", e)
            raise

    async def generate_failure_diagnosis(
        self,
        prompt_messages: list,
    ) -> FailureDiagnosisReport:
        logger.info("AnalysisAgent 生成最终失败诊断报告...")
        try:
            result = await self._failure_report_agent.run(prompt_messages)
            return result.output or FailureDiagnosisReport(
                executive_summary="模型未输出结构化报告",
                confidence="low",
            )
        except Exception as e:
            logger.error("AnalysisAgent 失败诊断报告生成失败: %s", e)
            raise
