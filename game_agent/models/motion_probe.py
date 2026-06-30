"""Motion probe 配置与结果模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

MotionRegionKind = Literal["pulsing_fixed", "moving_sprite", "swipe_hint"]


class MotionProbeSection(BaseModel):
    """局内 OpenCV 动效感知配置（仅 in_game_agent 使用）。"""

    enabled: bool = Field(True, description="总开关；生命周期门控仍须满足。")
    frame_count: int = Field(
        10,
        ge=2,
        le=20,
        description="连拍帧数；默认 10 帧 × 0.5s 间隔 ≈ 5s 动效窗口。",
    )
    interval_s: float = Field(
        0.5,
        ge=0.1,
        le=2.0,
        description="连拍间隔（秒）；默认 0.5s。",
    )
    save_heatmaps: bool = Field(True, description="是否在 artifact 目录保存热力图。")
    pulse_min_area: int = Field(200, ge=50, le=50000, description="闪烁候选最小连通域面积。")
    motion_min_area: int = Field(800, ge=100, le=100000, description="运动候选最小连通域面积。")
    ocr_fuse_radius_px: int = Field(120, ge=20, le=500, description="OCR 与动效热点融合半径。")
    always_burst: bool = Field(
        False,
        description="为 true 时局内每轮都 burst；默认由软门控决定。",
    )
    burst_on_forced_guidance: bool = Field(
        True,
        description="上轮 VLM 分析含 forced_guidance/pulse 建议时启用连拍。",
    )
    burst_on_no_progress: bool = Field(
        True,
        description="VLM/行为链无进展时下一轮强制连拍。",
    )
    hsv_yellow_boost: bool = Field(
        True,
        description="对黄色高亮指引光圈加权。",
    )
    hsv_white_glow_boost: bool = Field(
        True,
        description="对白色/低饱和高亮指引光圈加权（教程脉冲环）。",
    )
    fast_loop_enabled: bool = Field(
        False,
        description="行为链 intent 含 tap_glowing_hint 时启用快循环跟踪点击。",
    )
    fast_loop_max_frames: int = Field(15, ge=5, le=60, description="快循环最大截帧数。")
    fast_loop_interval_s: float = Field(0.1, ge=0.05, le=0.5, description="快循环截帧间隔。")


@dataclass(frozen=True, slots=True)
class MotionRegion:
    kind: MotionRegionKind
    cx: int
    cy: int
    bbox: tuple[int, int, int, int]
    area: int
    score: float
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MotionProbeResult:
    regions: list[MotionRegion]
    summary_text: str
    pairwise_mean_diff: float
    heatmap_path: Path | None = None
    pulse_overlay_path: Path | None = None
    annotated_path: Path | None = None
    swipe_direction: str = ""
    swipe_magnitude: float = 0.0
