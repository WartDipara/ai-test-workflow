from __future__ import annotations

from pydantic import BaseModel, Field


class GameEntryJudgment(BaseModel):
    """多模态对「是否已进入游戏内」的判定。"""

    in_game_main: bool = Field(
        description="True 表示已离开局外流程并处于游戏内场景（含强制新手引导蒙层）。",
    )
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    stage: str = Field(
        default="unknown",
        description=(
            "login | server_select | resource_download | loading | "
            "character_creation | tutorial_overlay | in_game_main | unknown"
        ),
    )
    ocr_signals: list[str] = Field(default_factory=list)
    reason: str = ""
    blockers: list[str] = Field(default_factory=list)
