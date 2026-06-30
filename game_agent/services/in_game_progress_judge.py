"""局内 VLM 进展判断与无进展 streak 管理。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from game_agent.models.in_game_progress import InGameSessionProgressJudgment
from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.launch_graph_state import LaunchGraphState
from game_agent.models.settings import AppConfig
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)

_GUIDED_STAGES = frozenset({"tutorial", "dialog", "unknown"})


@dataclass(frozen=True, slots=True)
class VlmProgressEvaluation:
    progressed: bool | None
    reason: str
    source: str
    after_analysis: InGameScreenAnalysis | None = None


def vlm_session_progressed(
    before: InGameScreenAnalysis | None,
    after: InGameScreenAnalysis | None,
    *,
    min_confidence: float,
) -> bool | None:
    """
    基于前后 InGameScreenAnalysis 规则判断进展。
    返回 True=推进, False=停滞, None=不确定（不计 streak）。
    """
    if after is None:
        return None
    if after.loading_or_blocking and after.confidence >= min_confidence:
        return None
    if before is not None and before.loading_or_blocking and not after.loading_or_blocking:
        if after.confidence >= min_confidence:
            return True

    conf = max(after.confidence, after.tap_confidence)
    if conf < min_confidence and before is not None:
        conf = max(conf, before.confidence)

    if before is not None and before.forced_guidance_present and not after.forced_guidance_present:
        if after.confidence >= min_confidence or conf >= min_confidence:
            return True

    if before is not None and before.ui_stage != after.ui_stage:
        if after.ui_stage in ("hud", "combat") or before.ui_stage in _GUIDED_STAGES:
            if conf >= min_confidence:
                return True

    if not after.screen_static and conf >= min_confidence:
        obs = f"{after.progress_observation} {after.observations}".lower()
        if after.forced_guidance_present and before is not None and before.forced_guidance_present:
            if obs and any(
                token in obs
                for token in ("change", "progress", "advanced", "selected", "checked", "deploy", "变化", "推进", "选中", "勾选", "上阵")
            ):
                return True
        if not after.forced_guidance_present:
            return True

    if (
        after.screen_static
        and after.forced_guidance_present
        and after.ui_stage in _GUIDED_STAGES
        and conf >= min_confidence
    ):
        return False

    return None


def apply_vlm_no_progress_streak(
    state: LaunchGraphState,
    progressed: bool | None,
    *,
    fail_threshold: int,
) -> tuple[int, bool]:
    streak = int(state.get("in_game_vlm_no_progress_streak") or 0)
    if progressed is True:
        streak = 0
    elif progressed is False:
        streak += 1
    state["in_game_vlm_no_progress_streak"] = streak
    return streak, streak >= fail_threshold


def store_progress_analysis(
    state: LaunchGraphState,
    analysis: InGameScreenAnalysis | None,
) -> None:
    if analysis is None:
        return
    state["last_in_game_progress_analysis"] = analysis.model_dump()


def load_progress_analysis(state: LaunchGraphState) -> InGameScreenAnalysis | None:
    raw = state.get("last_in_game_progress_analysis")
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return InGameScreenAnalysis.model_validate(raw)
    except Exception:
        return None


async def evaluate_in_game_session_progress(
    *,
    cfg: AppConfig,
    state: LaunchGraphState,
    before: InGameScreenAnalysis | None,
    after_shot: Path,
    before_ocr: str,
    after_ocr: str,
    round_id: int = 0,
    audit: RunAuditLogger | None = None,
) -> VlmProgressEvaluation:
    """动作后评估局内是否推进：优先 VLM judge，失败则 analyze + 规则。"""
    game = cfg.game
    min_conf = float(game.in_game_vlm_progress_min_confidence)
    after_analysis: InGameScreenAnalysis | None = None

    if game.in_game_post_action_vlm_analyze and cfg.llm_multimodal is not None:
        vision = VisionWorker(cfg.llm_multimodal)
        before_summary = ""
        if before is not None:
            before_summary = (
                f"ui_stage={before.ui_stage} forced_guidance={before.forced_guidance_present} "
                f"screen_static={before.screen_static} progress={before.progress_observation[:120]}"
            )
        try:
            judgment = await vision.judge_in_game_session_progress(
                screenshot_path=after_shot,
                before_ocr_summary=before_ocr,
                after_ocr_summary=after_ocr,
                before_analysis_summary=before_summary,
                round_id=round_id,
            )
            if judgment.confidence >= min_conf:
                progressed: bool | None = judgment.session_progressed
                reason = judgment.reason or "vlm_judge"
                if audit is not None:
                    audit.log_observer(
                        kind="in_game_vlm_progress",
                        message=reason[:500],
                        round_id=round_id,
                        extra={
                            "progressed": progressed,
                            "source": "vlm_judge",
                            "confidence": judgment.confidence,
                            "streak": int(state.get("in_game_vlm_no_progress_streak") or 0),
                        },
                    )
                return VlmProgressEvaluation(
                    progressed=progressed,
                    reason=reason,
                    source="vlm_judge",
                )
        except Exception:
            logger.exception("[in_game_progress] VLM judge failed, falling back to rules")

    if cfg.llm_multimodal is not None and game.in_game_post_action_vlm_analyze:
        from game_agent.services.in_game_screen_analyze import run_in_game_screen_analyze_on_capture

        result = await run_in_game_screen_analyze_on_capture(
            shot_path=after_shot,
            ocr_summary=after_ocr,
            cfg=cfg,
            state=state,
            round_id=round_id,
            audit=None,
            motion_summary="",
            spatial_hints="",
            annotated_path=None,
            bboxes=None,
            motion_result=None,
            use_cache=False,
            shot_hash="",
        )
        after_analysis = result.analysis

    progressed = vlm_session_progressed(before, after_analysis, min_confidence=min_conf)
    reason = "rules"
    if after_analysis is not None:
        reason = after_analysis.progress_observation or after_analysis.observations or reason
    if audit is not None:
        audit.log_observer(
            kind="in_game_vlm_progress",
            message=reason[:500],
            round_id=round_id,
            extra={
                "progressed": progressed,
                "source": "rules",
                "streak": int(state.get("in_game_vlm_no_progress_streak") or 0),
            },
        )
    return VlmProgressEvaluation(
        progressed=progressed,
        reason=reason[:300],
        source="rules",
        after_analysis=after_analysis,
    )


async def update_streak_after_action(
    state: LaunchGraphState,
    *,
    cfg: AppConfig,
    before: InGameScreenAnalysis | None,
    after_shot: Path,
    before_ocr: str,
    after_ocr: str,
    action: str,
    round_id: int = 0,
    audit: RunAuditLogger | None = None,
) -> tuple[int, bool, str]:
    """
    非 wait/none 动作后更新 streak。
    返回 (streak, force_fail, reason)。
    """
    if action in ("wait", "none"):
        return int(state.get("in_game_vlm_no_progress_streak") or 0), False, "skip_wait"

    evaluation = await evaluate_in_game_session_progress(
        cfg=cfg,
        state=state,
        before=before,
        after_shot=after_shot,
        before_ocr=before_ocr,
        after_ocr=after_ocr,
        round_id=round_id,
        audit=audit,
    )
    if evaluation.after_analysis is not None:
        store_progress_analysis(state, evaluation.after_analysis)

    threshold = int(cfg.game.in_game_vlm_no_progress_fail_rounds)
    streak, force_fail = apply_vlm_no_progress_streak(
        state,
        evaluation.progressed,
        fail_threshold=threshold,
    )
    reason = f"{evaluation.source}:{evaluation.reason}"
    logger.info(
        "[InGameProgress] action=%s progressed=%s streak=%d/%d | %s",
        action,
        evaluation.progressed,
        streak,
        threshold,
        reason[:160],
    )
    return streak, force_fail, reason
