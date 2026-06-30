"""VLM 动态场景标记：开放 label + 结构化执行策略。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from game_agent.models.scene_memory import SceneMemoryAction

SceneLabelScope = Literal["pre_enter", "in_game", "both"]
CoordStrategy = Literal["ocr", "pulse", "dim_region", "wait", "vlm_semantic", "none"]

_LEGACY_SCENE_HINTS = frozenset(
    {
        "dialogue",
        "tutorial",
        "loading",
        "character_creation",
        "character_select",
        "in_game_hud",
        "blocking_popup",
        "unknown",
    }
)

_VALID_COORD_STRATEGIES = frozenset(
    {"ocr", "pulse", "dim_region", "wait", "vlm_semantic", "none"}
)


def normalize_label_slug(raw: str) -> str:
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", (raw or "").strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:80] or "unknown_scene")


def normalize_coord_strategy(raw: str) -> CoordStrategy:
    s = (raw or "none").strip().lower()
    if s not in _VALID_COORD_STRATEGIES:
        return "none"
    return s  # type: ignore[return-value]


def legacy_scene_hint_from_slug(label_slug: str) -> str:
    """将开放 slug 映射到 legacy 路由 hint（dialogue/tutorial/loading）。"""
    slug = (label_slug or "").lower()
    if not slug or slug == "unknown_scene":
        return "unknown"
    if "loading" in slug or "spinner" in slug:
        return "loading"
    if any(k in slug for k in ("pulse", "tutorial", "glow", "battle_cta", "deploy")):
        return "tutorial"
    if any(k in slug for k in ("dialogue", "narrative", "story", "blank_continue")):
        return "dialogue"
    if "hud" in slug or "in_game" in slug:
        return "in_game_hud"
    return "tutorial" if "battle" in slug or "combat" in slug else "dialogue"


class SceneLabelJudgment(BaseModel):
    """VLM 场景标记输出（开放词汇，不截断为 unknown）。"""

    label_slug: str = "unknown_scene"
    label_display: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    coord_strategy: CoordStrategy = "none"
    semantic_target: str = ""
    match_prior_label_id: str = ""
    description: str = ""
    reason: str = ""
    use_dim_region_tap: bool = False
    dim_region_hint: str = ""
    legacy_scene_hint: str = ""

    def normalized_slug(self) -> str:
        return normalize_label_slug(self.label_slug)

    def normalized_coord_strategy(self) -> CoordStrategy:
        if self.use_dim_region_tap and self.coord_strategy == "none":
            return "dim_region"
        return normalize_coord_strategy(self.coord_strategy)

    def legacy_scene_id(self) -> str:
        hint = (self.legacy_scene_hint or "").strip().lower()
        if hint in _LEGACY_SCENE_HINTS:
            return hint
        return legacy_scene_hint_from_slug(self.normalized_slug())


class SceneLabelEntry(BaseModel):
    """注册表中一条可复现场景标记。"""

    label_id: str
    label_slug: str
    label_display: str = ""
    coord_strategy: CoordStrategy = "ocr"
    semantic_target: str = ""
    structural_fingerprint: str = ""
    ocr_skeleton: str = ""
    execution_policy: SceneMemoryAction = Field(default_factory=SceneMemoryAction)
    aliases: list[str] = Field(default_factory=list)
    scope: SceneLabelScope = "both"
    success_count: int = 0
    failure_count: int = 0
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    learned_at_round: int = 0
    source: str = "slow_path"
    screenshot_ref: str = ""
    notes: str = ""


class SceneLabelMatch(BaseModel):
    entry: SceneLabelEntry
    similarity: float = 0.0


class SceneLabelTraceEvent(BaseModel):
    """每轮 scene label 迭代记录。"""

    round_id: int = 0
    node: str = ""
    vlm_label_slug: str = ""
    vlm_label_display: str = ""
    matched_label_id: str = ""
    is_new_label: bool = False
    coord_strategy: str = ""
    semantic_target: str = ""
    tap_x: int = 0
    tap_y: int = 0
    progressed: bool | None = None
    screenshot_ref: str = ""
    ocr_head: str = ""
