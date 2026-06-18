"""小号选择 gate 裁决结果。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubAccountGateJudgment(BaseModel):
    is_sub_account: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    tap_x: int = 0
    tap_y: int = 0
    tap_label: str = ""
    reason: str = ""
