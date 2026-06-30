"""局内会话：主脑（llm）终局决策与行为链规划。"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic_ai import Agent

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.in_game_session_decision import (
    InGameSessionDecision,
    InGameSessionPlan,
    plan_to_decision,
    steps_plan_to_chain,
)
from game_agent.models.settings import DeepSeekSection, LLMSection
from game_agent.services.behavior_chain import BehaviorChain, BehaviorStep, behavior_step_from_vlm_analysis, merge_vlm_tap_into_chain, validate_behavior_chain
from game_agent.services.enter_gate_planner import format_ocr_candidates
from game_agent.services.in_game_agent import fallback_in_game_behavior_chain
from game_agent.services.llm_service import build_llm_model
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)

SESSION_GOAL = "完成新手强制引导后进入可自由操作的局内 HUD"


def _format_screen_analysis(analysis: InGameScreenAnalysis | None) -> str:
    if analysis is None:
        return "(VLM analysis unavailable)"
    return json.dumps(analysis.model_dump(), ensure_ascii=False, indent=2)


def _format_progress_context(
    *,
    round_id: int,
    elapsed_s: float,
    same_action_streak: int,
    behavior_no_progress: int,
    vlm_no_progress_streak: int,
    vlm_no_progress_fail_rounds: int,
    failure_trace: list[dict[str, Any]] | None,
    prior_verdict: str,
    prior_action_signature: str,
    active_chain_goal: str,
    active_step_intent: str,
    replan_from_step_id: str,
) -> str:
    failures = ""
    if failure_trace:
        failures = f"\nRecent failed steps:\n{json.dumps(failure_trace[-3:], ensure_ascii=False)}\n"
    return f"""
Progress context:
- round: {round_id}
- elapsed_s: {elapsed_s:.0f}
- same_action_streak: {same_action_streak}
- behavior_no_progress: {behavior_no_progress}
- vlm_no_progress_streak: {vlm_no_progress_streak} (hard fail at {vlm_no_progress_fail_rounds})
- prior_verdict: {prior_verdict or "none"}
- prior_action_signature: {prior_action_signature or "none"}
- active_chain_goal: {active_chain_goal or "none"}
- active_step_intent: {active_step_intent or "none"}
- replan_from_step_id: {replan_from_step_id or "none"}
{failures}"""


async def decide_in_game_session_round(
    *,
    llm_cfg: LLMSection | None,
    deepseek: DeepSeekSection | None,
    bboxes: list[OcrBbox],
    ocr_summary: str,
    screen_analysis: InGameScreenAnalysis | None,
    motion_summary: str = "",
    spatial_hints: str = "",
    round_id: int,
    elapsed_s: float,
    external_log_summary: str = "",
    failure_trace: list[dict[str, Any]] | None = None,
    replan_from_step_id: str = "",
    same_action_streak: int = 0,
    behavior_no_progress: int = 0,
    vlm_no_progress_streak: int = 0,
    vlm_no_progress_fail_rounds: int = 10,
    prior_verdict: str = "",
    prior_action_signature: str = "",
    active_chain_goal: str = "",
    active_step_intent: str = "",
    screen_w: int,
    screen_h: int,
    max_action_wait_s: float = 5.0,
) -> InGameSessionDecision:
    """主脑综合 VLM 分析与进展上下文，输出终局 verdict 与可选行为链。"""
    if llm_cfg is None:
        return _heuristic_decision(
            bboxes=bboxes,
            screen_analysis=screen_analysis,
            screen_w=screen_w,
            screen_h=screen_h,
        )

    candidates = format_ocr_candidates(bboxes)
    log_hint = ""
    if external_log_summary.strip():
        log_hint = f"\nExternal log excerpt:\n{external_log_summary[:1200]}\n"

    vlm_tap_hint = ""
    if screen_analysis is not None and screen_analysis.recommended_action not in ("none", "wait"):
        vlm_tap_hint = (
            f"\nVLM fused tap recommendation (prefer for first tap step):\n"
            f"  action={screen_analysis.recommended_action} "
            f"target={screen_analysis.tap_target_text!r} "
            f"@({screen_analysis.tap_x},{screen_analysis.tap_y}) "
            f"source={screen_analysis.tap_source} conf={screen_analysis.tap_confidence:.2f}\n"
            f"  reason: {screen_analysis.fusion_reason[:300]}\n"
        )

    progress = _format_progress_context(
        round_id=round_id,
        elapsed_s=elapsed_s,
        same_action_streak=same_action_streak,
        behavior_no_progress=behavior_no_progress,
        vlm_no_progress_streak=vlm_no_progress_streak,
        vlm_no_progress_fail_rounds=vlm_no_progress_fail_rounds,
        failure_trace=failure_trace,
        prior_verdict=prior_verdict,
        prior_action_signature=prior_action_signature,
        active_chain_goal=active_chain_goal,
        active_step_intent=active_step_intent,
        replan_from_step_id=replan_from_step_id,
    )

    prompt = f"""
You are the MAIN BRAIN controlling an Android game session already past login.
Session goal: {SESSION_GOAL}

You receive OCR and VLM screen analysis (including a fused tap recommendation from OpenCV+OCR).
Motion/spatial fusion was already done inside VLM — do NOT invent pulse coordinates.
YOU make the final verdict and plan multi-step actions.

OCR summary:
{ocr_summary}

OCR candidates (text + bbox):
{candidates}
{log_hint}{vlm_tap_hint}
VLM screen analysis (includes recommended_action / tap_x / tap_y):
{_format_screen_analysis(screen_analysis)}
{progress}

Verdict rules:
- success: no forced tutorial guidance; player can freely use normal HUD; high confidence
- fail: stuck with no safe next step after repeated no-progress (loading stuck, unknown blocker,
  or cannot plan any effective action); NOT for brief loading/animations
- If vlm_no_progress_streak is near the hard limit, prefer verdict=fail with clear reason
- wait: loading/animation in progress; set wait_s 1.0-{max_action_wait_s:.1f}
- continue: clear next steps exist; output 3-7 steps in steps[]

When verdict=continue:
- Read VLM target_has_ocr_semantics, semantic_target_text, recommended_coord_source.
- Each tap step MUST set coord_source:
  - ocr: target has readable OCR label — set target_text to FULL OCR row (战斗 not 战)
  - pulse: tutorial target has NO OCR text but visible pulse/glow/finger
  - vlm_xy: use VLM tap_x/tap_y exactly (motion_ocr_fused); do NOT remap single chars to wrong OCR rows
  - dialogue_blank: blank-continue dialogue
- When coord_source=vlm_xy or pulse, set tap_xy to VLM coordinates; do NOT substitute other OCR rows.
- When coord_source=ocr, use tap_text with full semantic_target_text OR tap_xy from matching OCR cx,cy.
- Do NOT use single-character target_text like 战 when 战斗 or 助战 exist — use full button label.
- Allowed actions: tap_xy, tap_text, swipe, press_back, wait, none
Each step needs intent + success_criteria + coord_source (for tap steps).
retry_strategy: none | replan | retry_step | wait_loading

Forbidden: credentials, install/uninstall, system settings, adb/shell.

JSON fields match output schema (verdict, confidence, reason, analysis, retry_strategy, wait_s, goal, steps).
"""
    try:
        model = build_llm_model(llm_cfg, deepseek=deepseek)
        agent = Agent(model, output_type=InGameSessionPlan)
        result = await agent.run(prompt)
        plan = result.output
        chain: BehaviorChain | None = None
        if plan.verdict == "continue" and plan.steps:
            chain = steps_plan_to_chain(
                plan,
                bboxes=bboxes,
                screen_w=screen_w,
                screen_h=screen_h,
                max_wait_s=max_action_wait_s,
            )
            if chain is None:
                chain = fallback_in_game_behavior_chain(bboxes)
        if plan.verdict == "continue" and screen_analysis is not None:
            chain = merge_vlm_tap_into_chain(
                chain,
                screen_analysis,
                bboxes=bboxes,
                screen_w=screen_w,
                screen_h=screen_h,
            )
            if chain is not None:
                chain = validate_behavior_chain(
                    chain,
                    bboxes=bboxes,
                    screen_w=screen_w,
                    screen_h=screen_h,
                )
        decision = plan_to_decision(plan, chain=chain, source="brain")
        logger.info(
            "[InGameBrain] round=%d verdict=%s conf=%.2f retry=%s steps=%d | %s",
            round_id,
            decision.verdict,
            decision.confidence,
            decision.retry_strategy,
            len(chain.steps) if chain else 0,
            decision.reason[:120],
        )
        return decision
    except Exception:
        logger.exception("[InGameBrain] main brain session decision failed")
        return _heuristic_decision(
            bboxes=bboxes,
            screen_analysis=screen_analysis,
            screen_w=screen_w,
            screen_h=screen_h,
        )


def _heuristic_decision(
    *,
    bboxes: list[OcrBbox],
    screen_analysis: InGameScreenAnalysis | None,
    screen_w: int,
    screen_h: int,
) -> InGameSessionDecision:
    if screen_analysis is not None:
        if (
            not screen_analysis.forced_guidance_present
            and screen_analysis.ui_stage in ("hud", "combat")
            and not screen_analysis.loading_or_blocking
        ):
            return InGameSessionDecision(
                verdict="success",
                confidence=0.6,
                reason="heuristic: no forced guidance on HUD",
                analysis="fallback brain",
                source="heuristic",
            )
        if screen_analysis.loading_or_blocking:
            return InGameSessionDecision(
                verdict="wait",
                confidence=0.5,
                reason="heuristic: loading or blocking UI",
                wait_s=2.0,
                source="heuristic",
            )
    vlm_step = behavior_step_from_vlm_analysis(
        screen_analysis,
        bboxes=bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
    ) if screen_analysis is not None else None
    if vlm_step is not None:
        chain = BehaviorChain(
            steps=[vlm_step, BehaviorStep(id="observe", action="wait", wait_s=1.5, intent="wait after tap")],
            source="vlm_fusion_heuristic",
            stage="in_game",
            goal="execute VLM fused tap",
        )
        chain = validate_behavior_chain(chain, bboxes=bboxes, screen_w=screen_w, screen_h=screen_h)
        if chain is not None:
            return InGameSessionDecision(
                verdict="continue",
                confidence=max(0.5, screen_analysis.tap_confidence if screen_analysis else 0.5),
                reason="heuristic: VLM fused tap",
                behavior_chain=chain,
                retry_strategy="replan",
                source="heuristic",
            )
    chain = fallback_in_game_behavior_chain(bboxes)
    if chain is not None:
        return InGameSessionDecision(
            verdict="continue",
            confidence=0.5,
            reason="heuristic: tap visible button",
            behavior_chain=chain,
            retry_strategy="replan",
            source="heuristic",
        )
    return InGameSessionDecision(
        verdict="wait",
        confidence=0.4,
        reason="heuristic: no safe action",
        wait_s=2.0,
        source="heuristic",
    )
