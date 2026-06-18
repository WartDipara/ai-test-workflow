"""场景 gate：VLM 仅描述/定性画面，坐标由 OCR 解析。"""

from __future__ import annotations

from pydantic import BaseModel, Field

VALID_SCENE_IDS = frozenset(
    {
        "dialogue",
        "tutorial",
        "loading",
        "character_creation",
        "character_select",
        "in_game_hud",
        "blocking_popup",
        "unknown",
    },
)

VALID_SCENE_ACTIONS = frozenset(
    {"wait", "tap_dialogue", "tap_skip", "tap_continue", "none"},
)


class SceneGateJudgment(BaseModel):
    """VLM 输出：画面语义与建议动作类型（不含坐标）。"""

    scene_id: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str = ""
    action: str = Field(
        default="none",
        description="wait | tap_dialogue | tap_skip | tap_continue | none — 坐标由 OCR 定位",
    )
    reason: str = ""

    def normalized_scene_id(self) -> str:
        sid = (self.scene_id or "unknown").strip().lower()
        if sid not in VALID_SCENE_IDS:
            return "unknown"
        return sid

    def normalized_action(self) -> str:
        act = (self.action or "none").strip().lower()
        if act not in VALID_SCENE_ACTIONS:
            return "none"
        return act

    def needs_ocr_tap(self) -> bool:
        return self.normalized_action() in ("tap_dialogue", "tap_skip", "tap_continue")
