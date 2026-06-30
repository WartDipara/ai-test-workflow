"""教程脉冲选点：VLM 判别结果与意图模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TutorialBand = Literal["top", "middle", "lower", ""]


class TutorialIntent(BaseModel):
    """OCR 检测到需要视觉定位的教程意图。"""

    kind: str = Field(default="tap_card", description="tap_card | deploy | tap_glow")
    trigger_phrase: str = ""
    reason: str = ""


class TutorialPulsePick(BaseModel):
    """VLM 对 motion_probe 脉冲候选的判别（不含像素坐标）。"""

    forced_guidance_present: bool = False
    chosen_pulse_rank: int = Field(default=0, ge=0, le=20)
    reject_ranks: list[int] = Field(default_factory=list)
    preferred_band: TutorialBand = ""
    target_description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
