from __future__ import annotations

from pydantic import BaseModel, Field


class CheckboxTapAlignmentJudgment(BaseModel):
    """多模态判断 tap 坐标是否落在协议 checkbox 上（而非文字）。"""

    on_checkbox: bool = Field(
        description="True 表示红点/tap 落在 checkbox 方框/圆圈内，而非协议文字上。",
    )
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    reason: str = ""
    adjust_direction: str = Field(
        default="ok",
        description="若未对准：left | right | up | down | ok",
    )
