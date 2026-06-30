"""Motion probe 生命周期与软门控策略。"""

from __future__ import annotations

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.launch_graph_state import LaunchGraphState
from game_agent.models.motion_probe import MotionProbeSection
from game_agent.models.settings import GameSection
from game_agent.services.tutorial_intent import (
    has_pulse_guidance_phrase,
    needs_visual_tap_locator,
)


def motion_probe_lifecycle_allowed(
    state: LaunchGraphState,
    *,
    cfg: GameSection | None = None,
) -> bool:
    """Session Agent 阶段允许连拍；旧路径仍要求 stability 完成。"""
    motion_cfg = _motion_cfg(cfg)
    if not motion_cfg.enabled:
        return False
    if state.get("in_game_agent_done"):
        return False

    if state.get("session_agent_active"):
        started = float(
            state.get("in_game_agent_started_at")
            or state.get("session_agent_started_at")
            or 0.0
        )
        return started > 0.0 or bool(state.get("session_agent_active"))

    if not state.get("in_game_entry_passed"):
        return False
    if not state.get("stability_observe_complete"):
        return False
    started = float(state.get("in_game_agent_started_at") or 0.0)
    if started <= 0.0:
        return False
    return True


def _last_screen_analysis(state: LaunchGraphState) -> InGameScreenAnalysis | None:
    raw = state.get("last_in_game_screen_analysis")
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return InGameScreenAnalysis.model_validate(raw)
    except Exception:
        return None


def _analysis_wants_burst(
    analysis: InGameScreenAnalysis,
    *,
    burst_on_forced_guidance: bool,
) -> bool:
    if not burst_on_forced_guidance:
        return False
    if analysis.forced_guidance_present and not analysis.target_has_ocr_semantics:
        return True
    if analysis.recommended_coord_source == "pulse":
        return True
    if analysis.ui_stage in ("tutorial", "combat") and analysis.forced_guidance_present:
        return True
    if analysis.tap_source in ("motion_pulse", "motion_ocr_fused") and analysis.tap_x <= 0:
        return True
    return False


def should_run_motion_burst(
    state: LaunchGraphState,
    *,
    cfg: GameSection | None = None,
) -> bool:
    """生命周期通过后，按轮次决定是否连拍。"""
    if not motion_probe_lifecycle_allowed(state, cfg=cfg):
        return False
    motion_cfg = _motion_cfg(cfg)
    if motion_cfg.always_burst:
        return True

    analysis = _last_screen_analysis(state)
    if analysis is not None and _analysis_wants_burst(
        analysis,
        burst_on_forced_guidance=motion_cfg.burst_on_forced_guidance,
    ):
        return True

    if motion_cfg.burst_on_no_progress:
        if int(state.get("in_game_vlm_no_progress_streak") or 0) >= 1:
            return True
        if int(state.get("in_game_behavior_no_progress") or 0) >= 1:
            return True

    facts = state.get("facts") or {}
    vision_stage = str(facts.get("vision_stage") or "").strip().lower()
    current_stage = str(state.get("current_stage") or "").strip().lower()
    if vision_stage == "tutorial_overlay" or "tutorial" in current_stage:
        return True

    judgment = state.get("game_entry_judgment") or {}
    if isinstance(judgment, dict):
        stage = str(judgment.get("stage") or "").strip().lower()
        if stage == "tutorial_overlay":
            return True

    if int(state.get("in_game_agent_same_action_streak") or 0) >= 2:
        return True

    if str(state.get("in_game_behavior_last_failed_step_id") or "").strip():
        return True

    scene_id = str(state.get("scene_id") or "").strip().lower()
    scene_conf = float(state.get("scene_confidence") or 0.0)
    if scene_id in ("tutorial", "unknown") or scene_conf < 0.6:
        return True

    last_ocr = str(state.get("last_ocr_summary") or "")
    if needs_visual_tap_locator(last_ocr):
        return True
    if has_pulse_guidance_phrase(last_ocr):
        return True

    return False


def _motion_cfg(cfg: GameSection | None) -> MotionProbeSection:
    if cfg is None:
        return MotionProbeSection()
    return cfg.motion_probe
