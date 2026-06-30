"""spatial_fuse 单元测试。"""

from __future__ import annotations

from game_agent.models.motion_probe import MotionProbeResult, MotionProbeSection, MotionRegion
from game_agent.services.spatial_fuse import spatial_fuse
from game_agent.utils.ocr_util import OcrBbox


def _pulse(cx: int, cy: int, score: float = 0.8) -> MotionRegion:
    return MotionRegion(
        kind="pulsing_fixed",
        cx=cx,
        cy=cy,
        bbox=(cx - 40, cy - 40, 80, 80),
        area=6400,
        score=score,
    )


def test_fuse_links_pulse_to_nearby_ocr() -> None:
    motion = MotionProbeResult(
        regions=[_pulse(981, 1612)],
        summary_text="test",
        pairwise_mean_diff=2.7,
    )
    bboxes = [
        OcrBbox(text="购买玄玉月卡", cx=920, cy=1580, x1=880, y1=1560, x2=960, y2=1600),
    ]
    fuse = spatial_fuse(bboxes, motion, motion_cfg=MotionProbeSection())
    assert fuse.top_tap_candidate == (981, 1612)
    assert fuse.top_tap_score > 0
    assert "购买玄玉月卡" in fuse.hints_text
    assert "tutorial_candidates" in fuse.hints_text


def test_fuse_motion_noise_without_pulse() -> None:
    motion = MotionProbeResult(
        regions=[
            MotionRegion(
                kind="moving_sprite",
                cx=423,
                cy=1256,
                bbox=(355, 1127, 149, 214),
                area=16328,
                score=0.6,
            ),
        ],
        summary_text="test",
        pairwise_mean_diff=2.7,
    )
    fuse = spatial_fuse([], motion)
    assert fuse.top_tap_candidate is None
    assert "motion_noise" in fuse.hints_text


def test_fuse_rank_prefers_ocr_proximity() -> None:
    motion = MotionProbeResult(
        regions=[
            _pulse(400, 1000, score=0.9),
            _pulse(980, 1610, score=0.7),
        ],
        summary_text="test",
        pairwise_mean_diff=2.7,
    )
    bboxes = [
        OcrBbox(text="购买", cx=970, cy=1590, x1=940, y1=1570, x2=1000, y2=1610),
    ]
    fuse = spatial_fuse(bboxes, motion)
    assert fuse.top_tap_candidate == (980, 1610)
