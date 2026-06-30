"""对话暗色遮罩区域点击配置。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DialogueDimTapSection(BaseModel):
    stall_threshold: int = Field(
        2,
        ge=1,
        le=10,
        description="对话 OCR 点击连续无进展多少次后允许启用暗色区域兜底。",
    )
    dark_percentile: float = Field(
        40.0,
        ge=10.0,
        le=60.0,
        description="灰度低于该百分位视为暗色像素。",
    )
    ocr_exclude_margin_px: int = Field(
        24,
        ge=0,
        le=80,
        description="OCR bbox 排除区膨胀像素。",
    )
    min_region_area_ratio: float = Field(
        0.05,
        ge=0.01,
        le=0.5,
        description="暗色连通域最小面积占屏比例。",
    )
    top_exclude_ratio: float = Field(0.12, ge=0.0, le=0.3)
    bottom_exclude_ratio: float = Field(0.08, ge=0.0, le=0.3)
    bright_dialogue_y_ratio: float = Field(
        0.55,
        ge=0.3,
        le=0.8,
        description="亮对话框搜索区域上边界（屏高比例）。",
    )
    prefer_tap_y_min_ratio: float = Field(0.55, ge=0.3, le=0.9)
    prefer_tap_y_max_ratio: float = Field(0.85, ge=0.5, le=0.95)
    save_annotate: bool = Field(True, description="是否保存暗区标注图。")
