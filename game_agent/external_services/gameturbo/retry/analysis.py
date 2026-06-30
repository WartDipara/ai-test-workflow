from __future__ import annotations

import json
import logging
from typing import Any

from pydantic_ai import Agent

from game_agent.exceptions import ConfigPatchGenerationError, ConfigPatchLlmError
from game_agent.models.deploy_recovery import DeployRecoveryPatch
from game_agent.models.failure_report import AttemptRoundDiagnosis, FailureDiagnosisReport
from game_agent.external_services.gameturbo.models.config import GameTurboConfigPatch
from game_agent.models.settings import DeepSeekSection, LLMSection
from game_agent.services.llm_service import build_llm_model
from game_agent.services.pipeline_trace import trace_operation
from game_agent.external_services.gameturbo.log.domain_extract import (
    format_domain_analysis_for_ai,
)
from game_agent.external_services.gameturbo.retry.prompts import (
    plugin_accel_log_prompt_block,
)

logger = logging.getLogger(__name__)

class AnalysisAgent:
    """
    Retry / failure structured analysis (config patches, deploy recovery, reports).

    Uses **main LLM** (``cfg.llm``) only — structured ``output_type=...`` maps to
    API tool_choice. Vision screenshots must be summarized to text separately
    (``vision_context.summarize_monitor_screenshots`` + ``llm_multimodal``).
    """
    def __init__(
        self,
        llm_config: LLMSection,
        *,
        deepseek: DeepSeekSection | None = None,
    ) -> None:
        self._llm_config = llm_config
        model = build_llm_model(llm_config, deepseek=deepseek)
        self._patch_agent = Agent(model, output_type=GameTurboConfigPatch)
        self._failure_report_agent = Agent(model, output_type=FailureDiagnosisReport)
        self._attempt_round_agent = Agent(model, output_type=AttemptRoundDiagnosis)
        self._deploy_recovery_agent = Agent(model, output_type=DeployRecoveryPatch)

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
You are the GameTurbo Android deploy.sh recovery assistant. Pack/inject/install failed; do not stop the pipeline — give actionable recovery.

Target gid: {gid}
Deploy attempt {attempt}/{max_attempts}.
Recent Python error summary: {last_error[:2000]}

Current game config JSON (games/gameturbo_{gid}_*.json):
{config_block}

deploy.sh log tail (stdout/stderr truncated):
{deploy_log_tail[-20000:]}

## Common failures

1. **Config not found** `No game config found` / `gameturbo_*`: verify game_id matches gid; valid JSON.
2. **merge_config / Python**: JSON syntax, required fields; fix then retry.
3. **build.sh / NDK / compile**: often env/transient — `retry_only=true`; note missing deps in analysis if explicit.
4. **APK missing**: check packages/; usually not fixable via games JSON — explain in analysis.
5. **adb / signing / install**: device/keystore — one retry_only may help.

## Output DeployRecoveryPatch

- **analysis** (required): root cause + recommended action.
- **retry_only**: true = no config change, retry deploy immediately.
- **game_id**: only if wrong in config — correct gid string.
- **direct_patterns / port_rules**: only if merge failed due to config content; leave empty for most build failures.

Do not suggest changing _platform, default_action, tunnel_patterns (invalid at deploy stage).
"""
        try:
            with trace_operation("llm", "analyze_deploy_failure", attempt=attempt) as rec:
                result = await self._deploy_recovery_agent.run(prompt)
                patch = result.output or DeployRecoveryPatch(
                    analysis="Model returned no deploy recovery patch",
                    retry_only=True,
                )
                rec.ok(retry_only=patch.retry_only, game_id=patch.game_id)
                return patch
        except Exception as e:
            logger.error("Deploy failure AI analysis error: %s", e)
            return DeployRecoveryPatch(
                analysis=f"AI analysis failed; retrying deploy: {e}",
                retry_only=True,
            )

    async def analyze_and_propose_patch(
        self,
        *,
        anomaly_reason: str,
        log_content: str,
        current_config: dict[str, Any],
        domain_analysis: dict[str, Any] | None = None,
        screen_context: str = "",
        blocked_stage_hint: str = "",
        prior_patch_restored: bool = False,
    ) -> GameTurboConfigPatch:
        logger.info("AnalysisAgent: generating GameTurbo config patch...")
        if not domain_analysis:
            raise ConfigPatchGenerationError(
                "Missing domain_region_analysis.json; need gameturbo.log export "
                "and successful extract_domain_region_from_log.",
                stage="domain_analysis",
            )

        domain_block = format_domain_analysis_for_ai(domain_analysis)
        current_config_block = json.dumps(current_config, ensure_ascii=False, indent=2)

        domain_priority_note = (
            "[Mandatory] Domain/region analysis below is from "
            "game_agent.external_services.gameturbo.log.domain_extract, "
            "aligned with GameTurbo-Native/extract_domain_region_from_log.sh + check_target_stability.py. "
            "direct_patterns: prefer direct_domains when sure they are resource/CDN/channel/SDK; "
            "no bulk direct for unknown_domains. "
            "Exception: tunnel_domains with CDN/resource naming (cdn/static/res/pkg) when failure is "
            "E2006/E2002 and gameturbo.log shows [SNI-TUNNEL] … -1 -1 1 on that domain — allow ONE "
            "direct_patterns trial entry; state verification in analysis. "
            "Never direct login/gateway/realm core domains. "
            "See log_ipv6_rule_direct block — do not add bare IPs to direct_patterns."
        )

        prompt = f"""
{plugin_accel_log_prompt_block()}

You are the GameTurbo network-acceleration config fix assistant. This project tests **tunnel acceleration**; only tunnel traffic is accelerated.
Bulk-adding business domains to direct_patterns may let the game connect but skips acceleration — wrong direction.

Modify stage. Do not create new config files; do not change game_id, _platform, default_action, tunnel_patterns.

## Allowed fields (only these two)

1. **direct_patterns** (usually only this): **append** domains or suffixes to the existing list.
2. **port_rules** (rare): merge by port; entries need integer port, action, etc. deploy accepts.

## direct_patterns admission (all must hold)

Add to direct only when **sure** the domain is:
- Game CDN / asset nodes (static, patch, bundle, image CDN; often large downloads or [SNI-DIRECT] unrelated to gameplay);
- Channel/SDK/analytics/crash (umeng, bugly, channel domains, etc.) with direct or unknown in JSON but clearly non-combat traffic;
- Obvious download/update names (cdn, download, res, static) with direct or unknown evidence.

**Do not** direct:
- Combat/login/realm/gateway/sync core domains (even if unknown — keep tunnel);
- Unknown domains bulk-directed just to connect;
- Domains already [SNI-TUNNEL] and healthy **when game flow also succeeded**;
- Domains not in domain JSON and without [SNI-DIRECT] in logs;
- Bare IPs from [IPV6-RULE] lines (need domain from PCAP first).

**CDN trial exception** (E2006/E2002 only):
- If `tunnel_domains` entry name suggests CDN/resource (`cdn`, `pkg`, `static`, `res`, …) AND log shows
  `[SNI-TUNNEL] <that-domain> … -1 -1 1` (default tunnel only) AND executor reports server/download/network UI failure:
  append **one** matching `direct_patterns` entry for the next retry; explain in `analysis`.
- Do not direct multiple CDN domains in one round; do not direct `ylx10pkg` and `ylx10cdn` together on first trial unless one already failed.

Keep default_action **tunnel**; do not propose changing it in the patch.
Do not use tunnel_patterns (not in this patch model); acceleration = default_action=tunnel + minimal direct allowlist.

## Decision order

1. Read domain/region JSON: direct_domains, tunnel_domains, unknown_domains, unmatched_pending_ips, non_china_domains.
2. Cross-check gameturbo.log [SNI-*] / [PENDING-SNI].
3. Append only resource/CDN/download/channel candidates to direct_patterns (dedupe; do not copy whole tables).
4. If none qualify, empty direct_patterns and explain in analysis.

{domain_priority_note}

Output GameTurboConfigPatch: analysis required; empty lists if no direct_patterns/port_rules changes.
Do not patch for baseline noise (heartbeat, recv buffer full, etc.).

## Minimal-change retry policy (mandatory)

- Each Modify round adds **at most a few** new `direct_patterns` entries (or **one** port_rules change), not bulk lists.
- If `prior_patch_restored` is true, the on-disk config was **reverted to pre-patch baseline** because the last patch did not fix the failure — propose a **different minimal** fix; do not re-apply the same domains.
- In `analysis`, state which failure stage you target (e.g. resource download) and how the new domains relate to logs/domain JSON.

## Verification focus

- If blocked at **resource download / update / CDN**: only add domains clearly tied to download/CDN/patch traffic seen in domain JSON or [SNI-DIRECT] for large transfers.
- Next game run must be judged on whether download completes — not merely whether the app opens.
"""
        if blocked_stage_hint.strip():
            prompt += f"\nLast run blocked stage hint: {blocked_stage_hint.strip()}\n"
        if prior_patch_restored:
            prompt += (
                "\n[System] Previous patch was rolled back before this proposal; "
                "config file is back to pre-patch state. Propose a new minimal diff only.\n"
            )
        prompt += f"""
Anomaly overview:
{anomaly_reason}

Current game config JSON:
{current_config_block}

Domain/region JSON (primary for Modify):
{domain_block}

GameTurbo log tail (auxiliary):
{log_content[-16000:]}
"""
        if screen_context.strip():
            prompt += f"\n\n{screen_context.strip()}\n"

        with trace_operation("llm", "analyze_and_propose_patch") as rec:
            try:
                result = await self._patch_agent.run(prompt)
            except Exception as e:
                logger.error("AnalysisAgent config patch failed: %s", e)
                rec.fail(error=str(e)[:500])
                raise ConfigPatchLlmError(f"AI config patch request failed: {e}") from e
            if result.output is None:
                rec.fail(error="empty_model_output")
                raise ConfigPatchLlmError(
                    "AI 未返回结构化 GameTurboConfigPatch（model output 为空）",
                )
            patch = result.output
            rec.ok(
                direct_patterns=len(patch.direct_patterns),
                port_rules=len(patch.port_rules),
            )
            return patch

    async def generate_attempt_round_diagnosis(
        self,
        prompt_messages: list,
    ) -> AttemptRoundDiagnosis:
        logger.info("AnalysisAgent: attempt failure diagnosis...")
        try:
            result = await self._attempt_round_agent.run(prompt_messages)
            return result.output or AttemptRoundDiagnosis(
                round_summary="Model returned no structured report",
                confidence="low",
            )
        except Exception as e:
            logger.error("AnalysisAgent attempt diagnosis failed: %s", e)
            raise

    async def generate_failure_diagnosis(
        self,
        prompt_messages: list,
    ) -> FailureDiagnosisReport:
        logger.info("AnalysisAgent: final failure diagnosis...")
        try:
            result = await self._failure_report_agent.run(prompt_messages)
            return result.output or FailureDiagnosisReport(
                executive_summary="Model returned no structured report",
                confidence="low",
            )
        except Exception as e:
            logger.error("AnalysisAgent failure diagnosis failed: %s", e)
            raise
