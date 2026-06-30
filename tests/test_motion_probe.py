"""motion_probe 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from game_agent.models.motion_probe import MotionProbeSection
from game_agent.services.motion_probe import run_motion_probe
from game_agent.services.motion_probe_policy import (
    motion_probe_lifecycle_allowed,
    should_run_motion_burst,
)
from game_agent.models.launch_graph_state import empty_launch_graph_state


@pytest.fixture
def sample_frames() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    paths = sorted(root.glob("screenshot_20260626_095310_*.png"), key=lambda p: p.name)
    if len(paths) < 2:
        pytest.skip("sample burst screenshots not in repo root")
    return paths


def test_motion_probe_finds_pulsing_and_motion_regions(sample_frames: list[Path], tmp_path: Path) -> None:
    result = run_motion_probe(
        sample_frames,
        artifact_root=tmp_path,
        round_id=1,
        motion_cfg=MotionProbeSection(save_heatmaps=True),
    )
    assert result.pairwise_mean_diff > 0.5
    pulsing = [r for r in result.regions if r.kind == "pulsing_fixed"]
    moving = [r for r in result.regions if r.kind == "moving_sprite"]
    assert pulsing, "expected at least one pulsing_fixed region"
    assert moving, "expected at least one moving_sprite region"
    # 右下指引光效区域（实测约 981,1612）
    assert any(r.cx > 800 and r.cy > 1400 for r in pulsing)
    # 中央蝎子 idle（实测约 423-561, 992-1175）
    assert any(r.cx < 650 and r.cy < 1300 for r in moving)
    assert result.heatmap_path is not None
    assert result.heatmap_path.exists()
    assert "pulsing_fixed" in result.summary_text


def test_lifecycle_denied_before_in_game() -> None:
    state = empty_launch_graph_state()
    assert motion_probe_lifecycle_allowed(state) is False
    assert should_run_motion_burst(state) is False


def test_lifecycle_allowed_after_in_game_session() -> None:
    state = empty_launch_graph_state()
    state["in_game_entry_passed"] = True
    state["stability_observe_complete"] = True
    state["in_game_agent_started_at"] = 1.0
    assert motion_probe_lifecycle_allowed(state) is True


def test_lifecycle_allowed_session_agent_without_stability() -> None:
    state = empty_launch_graph_state()
    state["session_agent_active"] = True
    state["session_agent_started_at"] = 1.0
    state["in_game_agent_started_at"] = 1.0
    assert motion_probe_lifecycle_allowed(state) is True


def test_soft_burst_on_tutorial_stage() -> None:
    state = empty_launch_graph_state()
    state["in_game_entry_passed"] = True
    state["stability_observe_complete"] = True
    state["in_game_agent_started_at"] = 1.0
    state["facts"] = {"vision_stage": "tutorial_overlay"}
    assert should_run_motion_burst(state) is True


def test_soft_burst_off_stable_hud() -> None:
    state = empty_launch_graph_state()
    state["in_game_entry_passed"] = True
    state["stability_observe_complete"] = True
    state["in_game_agent_started_at"] = 1.0
    state["scene_id"] = "in_game_hud"
    state["scene_confidence"] = 0.95
    state["in_game_agent_same_action_streak"] = 0
    state["in_game_behavior_last_failed_step_id"] = ""
    assert should_run_motion_burst(state) is False


def test_soft_burst_combat_forced_guidance_pulse() -> None:
    state = empty_launch_graph_state()
    state["in_game_entry_passed"] = True
    state["stability_observe_complete"] = True
    state["in_game_agent_started_at"] = 1.0
    state["scene_id"] = "in_game_hud"
    state["scene_confidence"] = 0.85
    state["last_in_game_screen_analysis"] = {
        "ui_stage": "combat",
        "forced_guidance_present": True,
        "target_has_ocr_semantics": False,
        "recommended_coord_source": "pulse",
        "tap_source": "motion_ocr_fused",
        "tap_x": 0,
        "tap_y": 0,
        "confidence": 0.8,
    }
    assert should_run_motion_burst(state) is True


def test_soft_burst_on_vlm_no_progress() -> None:
    state = empty_launch_graph_state()
    state["in_game_entry_passed"] = True
    state["stability_observe_complete"] = True
    state["in_game_agent_started_at"] = 1.0
    state["scene_id"] = "in_game_hud"
    state["scene_confidence"] = 0.95
    state["in_game_vlm_no_progress_streak"] = 3
    assert should_run_motion_burst(state) is True


def test_soft_burst_on_pulse_guidance_ocr() -> None:
    state = empty_launch_graph_state()
    state["in_game_entry_passed"] = True
    state["stability_observe_complete"] = True
    state["in_game_agent_started_at"] = 1.0
    state["scene_id"] = "in_game_hud"
    state["scene_confidence"] = 0.85
    state["last_ocr_summary"] = "点我放必杀！"
    assert should_run_motion_burst(state) is True
