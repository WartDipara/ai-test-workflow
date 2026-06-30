"""局内 VLM 画面分析（描述性 + motion/OCR 融合点击建议）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.launch_graph_state import LaunchGraphState
from game_agent.models.motion_probe import MotionProbeResult
from game_agent.models.settings import AppConfig
from game_agent.services.enter_gate_planner import format_ocr_candidates
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.services.tutorial_intent import detect_tutorial_visual_intent, needs_visual_tap_locator
from game_agent.services.tutorial_pulse_locator import (
    apply_motion_pulse_to_analysis,
    resolve_tutorial_visual_tap,
)
from game_agent.utils.ocr_util import OcrBbox, deserialize_bboxes
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InGameScreenAnalyzeResult:
    analysis: InGameScreenAnalysis | None
    message: str


async def _apply_tutorial_pulse_overlay(
    vision: VisionWorker,
    analysis: InGameScreenAnalysis,
    *,
    shot_path: Path,
    ocr_summary: str,
    bboxes: list[OcrBbox],
    motion_result: MotionProbeResult | None,
    motion_summary: str,
    annotated_path: Path | None,
    screen_w: int,
    screen_h: int,
    round_id: int,
) -> InGameScreenAnalysis:
    if not needs_visual_tap_locator(ocr_summary, bboxes):
        return analysis

    intent = detect_tutorial_visual_intent(ocr_summary, bboxes)
    vlm_pick = None
    if motion_summary.strip() and annotated_path is not None and annotated_path.is_file():
        vlm_pick = await vision.judge_tutorial_pulse(
            screenshot_path=shot_path,
            ocr_summary=ocr_summary,
            motion_summary=motion_summary,
            tutorial_intent=intent.trigger_phrase if intent else "",
            annotated_path=annotated_path,
            round_id=round_id,
        )

    tap = resolve_tutorial_visual_tap(
        motion=motion_result,
        screenshot_path=shot_path,
        screen_w=screen_w,
        screen_h=screen_h,
        ocr_summary=ocr_summary,
        bboxes=bboxes,
        vlm_pick=vlm_pick,
    )
    if tap is None:
        logger.info(
            "[in_game_screen_analyze] tutorial visual intent but no pulse tap | round=%d",
            round_id,
        )
        return analysis

    enriched = apply_motion_pulse_to_analysis(analysis, tap, vlm_pick=vlm_pick)
    logger.info(
        "[in_game_screen_analyze] motion_pulse tap (%d,%d) | round=%d | %s",
        tap.x,
        tap.y,
        round_id,
        tap.reason,
    )
    return enriched


async def run_in_game_screen_analyze_on_capture(
    *,
    shot_path: Path,
    ocr_summary: str,
    cfg: AppConfig,
    state: LaunchGraphState,
    round_id: int = 0,
    audit: RunAuditLogger | None = None,
    motion_summary: str = "",
    spatial_hints: str = "",
    annotated_path: Path | None = None,
    bboxes: list[OcrBbox] | None = None,
    motion_result: MotionProbeResult | None = None,
    screen_w: int = 0,
    screen_h: int = 0,
    use_cache: bool = True,
    shot_hash: str = "",
    attempt_context=None,
) -> InGameScreenAnalyzeResult:
    """对已有截图 + OCR（+ motion）做局内画面融合分析，写入 state。"""
    llm_cfg = cfg.llm_multimodal
    if llm_cfg is None:
        return InGameScreenAnalyzeResult(
            analysis=None,
            message="llm_multimodal not configured",
        )

    cached_hash = str(state.get("in_game_analyze_cache_hash") or "")
    cached = state.get("last_in_game_screen_analysis")
    if use_cache and shot_hash and shot_hash == cached_hash and isinstance(cached, dict):
        try:
            analysis = InGameScreenAnalysis.model_validate(cached)
            return InGameScreenAnalyzeResult(analysis=analysis, message="cached")
        except Exception:
            pass

    raw_bboxes = bboxes
    if raw_bboxes is None:
        raw_bboxes = deserialize_bboxes(state.get("last_bboxes") or [])
    ocr_candidates_json = format_ocr_candidates(raw_bboxes or [])

    from game_agent.modules.session_invalidation import capture_session_generation, discard_if_stale

    work_gen = capture_session_generation(attempt_context)
    vision = VisionWorker(llm_cfg, attempt_context=attempt_context)
    try:
        analysis = await vision.analyze_in_game_screen(
            screenshot_path=shot_path,
            ocr_summary=ocr_summary,
            ocr_candidates_json=ocr_candidates_json,
            motion_summary=motion_summary,
            spatial_hints=spatial_hints,
            annotated_path=annotated_path,
            round_id=round_id,
        )
    except Exception as e:
        logger.exception("[in_game_screen_analyze] multimodal API failed")
        return InGameScreenAnalyzeResult(
            analysis=None,
            message=str(e)[:500],
        )
    if discard_if_stale(work_gen, where="in_game_screen_analyze", ctx=attempt_context):
        return InGameScreenAnalyzeResult(analysis=None, message="stale_session_discard")

    analysis = await _apply_tutorial_pulse_overlay(
        vision,
        analysis,
        shot_path=shot_path,
        ocr_summary=ocr_summary,
        bboxes=raw_bboxes or [],
        motion_result=motion_result,
        motion_summary=motion_summary,
        annotated_path=annotated_path,
        screen_w=screen_w,
        screen_h=screen_h,
        round_id=round_id,
    )
    if discard_if_stale(work_gen, where="in_game_screen_analyze:pulse", ctx=attempt_context):
        return InGameScreenAnalyzeResult(analysis=None, message="stale_session_discard")

    state["last_in_game_screen_analysis"] = analysis.model_dump()
    if shot_hash:
        state["in_game_analyze_cache_hash"] = shot_hash

    if audit is not None:
        audit.log_observer(
            kind="in_game_screen_analyze",
            message=analysis.fusion_reason[:500]
            or analysis.observations[:500]
            or analysis.analysis[:500],
            round_id=round_id,
            extra=analysis.model_dump(),
        )

    logger.info(
        "[in_game_screen_analyze] round=%d stage=%s guidance=%s tap=%s@(%d,%d) src=%s conf=%.2f",
        round_id,
        analysis.ui_stage,
        analysis.forced_guidance_present,
        analysis.recommended_action,
        analysis.tap_x,
        analysis.tap_y,
        analysis.tap_source,
        analysis.tap_confidence,
    )
    return InGameScreenAnalyzeResult(analysis=analysis, message=analysis.analysis or analysis.observations)
