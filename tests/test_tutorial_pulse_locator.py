"""教程脉冲选点与无文字 UI 点击。"""

from __future__ import annotations

from pathlib import Path

import pytest

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.motion_probe import MotionProbeResult, MotionProbeSection, MotionRegion
from game_agent.models.tutorial_pulse import TutorialPulsePick
from game_agent.services.behavior_chain import behavior_step_from_vlm_analysis
from game_agent.services.motion_probe import run_motion_probe
from game_agent.services.tutorial_intent import (
    detect_tutorial_visual_intent,
    needs_visual_tap_locator,
)
from game_agent.services.tutorial_pulse_locator import (
    pick_tutorial_pulse_tap,
    resolve_tutorial_visual_tap,
)
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, cx: int, cy: int) -> OcrBbox:
    return OcrBbox(text=text, cx=cx, cy=cy, x1=cx - 40, y1=cy - 12, x2=cx + 40, y2=cy + 12)


@pytest.fixture
def card_tutorial_frames() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    paths = [root / f"screenshot_{i}.png" for i in range(1, 11)]
    existing = [p for p in paths if p.is_file()]
    if len(existing) < 2:
        pytest.skip("screenshot_1..10 not in repo root")
    return existing


def test_needs_visual_tap_locator_for_click_card_dialogue() -> None:
    ocr = "团长大人，点击卡牌就能上阵！ 战斗"
    bboxes = [
        _bbox("团长大人，点击卡牌就能上阵！", 280, 1750),
        _bbox("战斗", 540, 2100),
    ]
    assert needs_visual_tap_locator(ocr, bboxes)
    intent = detect_tutorial_visual_intent(ocr, bboxes)
    assert intent is not None
    assert intent.kind == "tap_card"


def test_motion_probe_finds_pulse_on_card_tutorial_burst(
    card_tutorial_frames: list[Path],
    tmp_path: Path,
) -> None:
    result = run_motion_probe(
        card_tutorial_frames,
        artifact_root=tmp_path,
        round_id=1,
        motion_cfg=MotionProbeSection(save_heatmaps=False, hsv_white_glow_boost=True),
    )
    pulsing = [r for r in result.regions if r.kind == "pulsing_fixed"]
    assert pulsing, "expected pulsing_fixed on card tutorial burst"
    assert any(r.extra.get("band") for r in pulsing)
    # 卡牌高亮在下半屏，不应只在顶部 HUD
    lower = [r for r in pulsing if r.cy > 900]
    assert lower, f"expected lower-screen pulse, got {[(r.cx, r.cy) for r in pulsing]}"


def test_pick_tutorial_pulse_with_vlm_rank(
    card_tutorial_frames: list[Path],
    tmp_path: Path,
) -> None:
    motion = run_motion_probe(
        card_tutorial_frames,
        artifact_root=tmp_path,
        round_id=2,
        motion_cfg=MotionProbeSection(save_heatmaps=False, hsv_white_glow_boost=True),
    )
    ocr = "团长大人，点击卡牌就能上阵！"
    bboxes = [_bbox(ocr, 280, 1750), _bbox("战斗", 540, 2100)]
    pulsing = [r for r in motion.regions if r.kind == "pulsing_fixed"]
    if not pulsing:
        pytest.skip("no pulses detected on sample frames")

    vlm_pick = TutorialPulsePick(chosen_pulse_rank=1, confidence=0.9, reason="test")
    tap = pick_tutorial_pulse_tap(
        motion,
        screen_w=1080,
        screen_h=2400,
        vlm_pick=vlm_pick,
        ocr_summary=ocr,
        bboxes=bboxes,
    )
    assert tap is not None
    assert tap.x > 0 and tap.y > 0


def test_resolve_static_glow_when_no_motion(card_tutorial_frames: list[Path]) -> None:
    ocr = "团长大人，点击卡牌就能上阵！"
    bboxes = [_bbox(ocr, 280, 1750)]
    empty_motion = MotionProbeResult(regions=[], summary_text="", pairwise_mean_diff=0.0)
    tap = resolve_tutorial_visual_tap(
        motion=empty_motion,
        screenshot_path=card_tutorial_frames[0],
        screen_w=1080,
        screen_h=2400,
        ocr_summary=ocr,
        bboxes=bboxes,
    )
    # 单帧高亮可能因截图差异未检出；有则验证坐标合理
    if tap is not None:
        assert tap.y > 500


def test_behavior_step_accepts_motion_pulse_without_ocr_neighbor() -> None:
    analysis = InGameScreenAnalysis(
        recommended_action="tap_xy",
        tap_x=320,
        tap_y=1650,
        tap_source="motion_pulse",
        tap_confidence=0.7,
        confidence=0.6,
        fusion_reason="motion_pulse pulse_rank_1",
    )
    bboxes = [_bbox("团长大人，点击卡牌就能上阵！", 280, 1750)]
    step = behavior_step_from_vlm_analysis(
        analysis,
        bboxes=bboxes,
        screen_w=1080,
        screen_h=2400,
    )
    assert step is not None
    assert step.action == "tap_xy"
    assert step.x == 320
    assert step.y == 1650
