from __future__ import annotations

from pydantic import BaseModel, Field


class PrivacyCheckboxJudgment(BaseModel):
    """多模态对协议 checkbox 勾选状态的判定。"""

    state: str = Field(
        default="uncertain",
        description="checked | unchecked | not_found | uncertain",
    )
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    checkbox_visible: bool = False
    reason: str = ""
    suggested_action: str = Field(
        default="none",
        description="tap_checkbox | tap_consent_button | none",
    )
    tap_x: int = 0
    tap_y: int = 0
    tap_label: str = ""

    @property
    def is_checked(self) -> bool:
        return self.state == "checked"

    @property
    def is_unchecked(self) -> bool:
        return self.state == "unchecked"

    def suggests_consent_button(self, *, min_confidence: float = 0.55) -> bool:
        return (
            self.state == "not_found"
            and self.confidence >= min_confidence
            and self.suggested_action == "tap_consent_button"
        )
