"""场景识别与策略执行的数据模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SceneId = Literal[
    "dialogue",
    "tutorial",
    "character_select",
    "character_creation",
    "loading",
    "in_game_hud",
    "blocking_popup",
    "unknown",
]

SceneTransitionKind = Literal[
    "none",
    "scene_changed",
    "animation_or_loading",
    "blocking_popup",
    "low_confidence",
    "exit_to_game",
]

SceneActionKind = Literal["tap_xy", "wait", "press_back", "observe", "none"]

SCENE_STRATEGY_IDS: frozenset[str] = frozenset({"dialogue", "tutorial", "loading"})


class SceneClassification(BaseModel):
    scene_id: SceneId = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str = ""
    fingerprint: str = ""
    source: str = "rule"


class SceneTransition(BaseModel):
    kind: SceneTransitionKind = "none"
    reason: str = ""
    from_scene: str = ""
    to_scene: str = ""


class SceneActionPlan(BaseModel):
    action: SceneActionKind = "none"
    x: int = 0
    y: int = 0
    wait_s: float = Field(default=1.5, ge=0.5, le=8.0)
    target_text: str = ""
    reason: str = ""
    mode: str = "advance"

    def signature(self) -> str:
        return f"{self.action}:{self.x}:{self.y}:{self.wait_s:.1f}:{self.mode}"
