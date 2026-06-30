from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

UiStage = Literal[
    "tutorial",
    "combat",
    "dialog",
    "loading",
    "hud",
    "unknown",
]

TapSource = Literal["none", "ocr_bbox", "motion_ocr_fused", "motion_pulse", "dialogue_blank"]
RecommendedCoordSource = Literal["ocr", "pulse", "vlm_xy", "dialogue_blank", "none"]
RecommendedAction = Literal["tap_xy", "tap_text", "swipe", "wait", "none"]


class InGameScreenAnalysis(BaseModel):
    """VLM 局内画面分析 + motion/OCR 融合点击建议。"""

    forced_guidance_present: bool = Field(
        default=False,
        description="是否可见强制引导（手指、遮罩、脉冲 CTA 等）。",
    )
    guidance_signals: list[str] = Field(
        default_factory=list,
        description="如 finger_hint, mask_overlay, pulsing_cta, swipe_guide。",
    )
    ui_stage: UiStage = "unknown"
    screen_static: bool = Field(
        default=False,
        description="画面是否几乎静止（台词/卡牌文案变化不算 static）。",
    )
    loading_or_blocking: bool = Field(
        default=False,
        description="加载圈、全屏遮罩、未知阻塞弹窗等。",
    )
    progress_observation: str = Field(
        default="",
        description="相对上轮是否有可见进展的观察（描述性）。",
    )
    observations: str = Field(
        default="",
        description="2-4 句画面描述。",
    )
    analysis: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    recommended_action: RecommendedAction = Field(
        default="none",
        description="融合 motion 热点与 OCR 后的推荐动作。",
    )
    tap_target_text: str = Field(
        default="",
        description="推荐点击的 OCR 文本行（tap_text 或锚定 OCR）。",
    )
    tap_x: int = Field(default=0, description="推荐点击 x（设备像素）。")
    tap_y: int = Field(default=0, description="推荐点击 y（设备像素）。")
    tap_x2: int = Field(default=0, description="swipe 终点 x。")
    tap_y2: int = Field(default=0, description="swipe 终点 y。")
    tap_source: TapSource = Field(
        default="none",
        description="坐标来源：ocr_bbox / motion_ocr_fused / motion_pulse / dialogue_blank。",
    )
    fusion_reason: str = Field(
        default="",
        description="为何选择该点击点（对比 P 热点与 OCR）。",
    )
    rejected_pulses: list[str] = Field(
        default_factory=list,
        description="排除的噪声热点及原因。",
    )
    tap_confidence: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="融合点击建议置信度。",
    )
    use_dim_region_tap: bool = Field(
        default=False,
        description="是否建议启用暗色压暗背景区域点击兜底。",
    )
    dim_region_hint: str = Field(
        default="",
        description="暗色区域点击提示原因。",
    )
    target_has_ocr_semantics: bool = Field(
        default=False,
        description="可点击目标是否有独立 OCR 文案（如按钮「战斗」）。",
    )
    semantic_target_text: str = Field(
        default="",
        description="完整 OCR 目标文案（优先于 tap_target_text 单字片段）。",
    )
    recommended_coord_source: RecommendedCoordSource = Field(
        default="none",
        description="建议坐标绑定策略：ocr / pulse / vlm_xy / dialogue_blank。",
    )
