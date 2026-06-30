"""主脑局内决策 streak 确认（success / fail）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from game_agent.models.in_game_session_decision import InGameSessionDecision, VerdictType
from game_agent.models.launch_graph_state import LaunchGraphState
from game_agent.models.settings import AppConfig
from game_agent.services.run_audit_log import RunAuditLogger

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InGameBrainRoundResult:
    decision: InGameSessionDecision
    success_streak: int
    fail_streak: int
    success_confirmed: bool
    fail_confirmed: bool
    success_confirm_needed: int
    fail_confirm_needed: int


def _update_verdict_streaks(
    state: LaunchGraphState,
    *,
    verdict: VerdictType,
    confidence: float,
    success_min_conf: float,
    fail_min_conf: float,
) -> tuple[int, int]:
    success_streak = int(state.get("in_game_brain_success_streak") or 0)
    fail_streak = int(state.get("in_game_brain_fail_streak") or 0)

    if verdict == "success" and confidence >= success_min_conf:
        success_streak += 1
        fail_streak = 0
    elif verdict == "fail" and confidence >= fail_min_conf:
        fail_streak += 1
        success_streak = 0
    else:
        success_streak = 0
        fail_streak = 0

    state["in_game_brain_success_streak"] = success_streak
    state["in_game_brain_fail_streak"] = fail_streak
    return success_streak, fail_streak


def apply_brain_decision_to_state(
    state: LaunchGraphState,
    decision: InGameSessionDecision,
    *,
    cfg: AppConfig,
    audit: RunAuditLogger | None = None,
    round_id: int = 0,
) -> InGameBrainRoundResult:
    game = cfg.game
    success_need = int(game.in_game_success_confirm_rounds)
    fail_need = int(game.in_game_fail_confirm_rounds)
    success_min = float(game.in_game_success_min_confidence)
    fail_min = float(game.in_game_fail_min_confidence)

    state["last_in_game_session_decision"] = decision.model_dump(mode="json")
    state["in_game_brain_last_verdict"] = decision.verdict

    success_streak, fail_streak = _update_verdict_streaks(
        state,
        verdict=decision.verdict,
        confidence=decision.confidence,
        success_min_conf=success_min,
        fail_min_conf=fail_min,
    )

    if audit is not None:
        audit.log_observer(
            kind="in_game_brain_decision",
            message=decision.reason[:500],
            round_id=round_id,
            extra={
                **decision.model_dump(mode="json"),
                "success_streak": success_streak,
                "fail_streak": fail_streak,
            },
        )

    success_confirmed = success_streak >= success_need
    fail_confirmed = fail_streak >= fail_need

    if success_confirmed:
        logger.info(
            "[InGameBrain] success confirmed streak=%d conf=%.2f | %s",
            success_streak,
            decision.confidence,
            decision.reason[:160],
        )
    elif fail_confirmed:
        logger.info(
            "[InGameBrain] fail confirmed streak=%d conf=%.2f | %s",
            fail_streak,
            decision.confidence,
            decision.reason[:160],
        )

    return InGameBrainRoundResult(
        decision=decision,
        success_streak=success_streak,
        fail_streak=fail_streak,
        success_confirmed=success_confirmed,
        fail_confirmed=fail_confirmed,
        success_confirm_needed=success_need,
        fail_confirm_needed=fail_need,
    )
