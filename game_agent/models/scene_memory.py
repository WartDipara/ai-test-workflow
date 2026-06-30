"""局内场景临时记忆（artifacts RAG）数据模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SceneArchetype = Literal[
    "technique_selection",
    "dialogue_blank_continue",
    "dialogue_narrative",
    "unknown",
]

MemoryActionResolver = Literal[
    "fixed_xy",
    "screen_ratio",
    "center_card_column",
    "dim_region",
]


class SceneMemoryAction(BaseModel):
    action: Literal["tap_xy", "wait"] = "tap_xy"
    resolver: MemoryActionResolver = "screen_ratio"
    x: int = 0
    y: int = 0
    x_ratio: float = Field(0.0, ge=0.0, le=1.0)
    y_ratio: float = Field(0.0, ge=0.0, le=1.0)
    wait_s: float = 0.8
    intent: str = ""


class SceneMemoryEntry(BaseModel):
    """单条可复现场景记忆。"""

    memory_id: str
    archetype: SceneArchetype
    structural_fingerprint: str = ""
    ocr_skeleton: str = ""
    primary_action: SceneMemoryAction
    verify: Literal["archetype_gone", "ocr_progress"] = "archetype_gone"
    success_count: int = 0
    failure_count: int = 0
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    learned_at_round: int = 0
    source: str = "slow_path"
    screenshot_ref: str = ""
    notes: str = Field(
        default="",
        description="仅记录已验证成功的学习来源；失败尝试不会写入 memories.jsonl。",
    )


class SceneMemoryMatch(BaseModel):
    entry: SceneMemoryEntry
    similarity: float = 0.0
    archetype: SceneArchetype = "unknown"
