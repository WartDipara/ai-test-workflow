"""隐私门禁判别：冷启动弹窗 vs 登录页协议 checkbox。"""

from __future__ import annotations

from pydantic import BaseModel, Field

PrivacyGateKind = str  # modal | checkbox | none | unknown


class PrivacyGateJudgment(BaseModel):
    gate_kind: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    tap_x: int = 0
    tap_y: int = 0
    tap_label: str = ""
    reason: str = ""

    def is_modal(self, *, min_confidence: float = 0.55) -> bool:
        return self.gate_kind == "modal" and self.confidence >= min_confidence

    def is_checkbox(self, *, min_confidence: float = 0.55) -> bool:
        return self.gate_kind == "checkbox" and self.confidence >= min_confidence
