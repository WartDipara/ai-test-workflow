"""场景 gate：VLM 开放场景标记 + legacy 兼容字段。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from game_agent.models.scene_label import SceneLabelJudgment, legacy_scene_hint_from_slug, normalize_label_slug

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
    """VLM 输出：开放 label_slug + coord_strategy；保留 legacy scene_id/action。"""

    label_slug: str = ""
    label_display: str = ""
    coord_strategy: str = "none"
    semantic_target: str = ""
    match_prior_label_id: str = ""
    legacy_scene_hint: str = ""
    scene_id: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description: str = ""
    action: str = Field(default="none")
    reason: str = ""
    use_dim_region_tap: bool = False
    dim_region_hint: str = ""

    def normalized_slug(self) -> str:
        slug = normalize_label_slug(self.label_slug)
        if slug != "unknown_scene":
            return slug
        sid = (self.scene_id or "unknown").strip().lower()
        if sid in VALID_SCENE_IDS and sid != "unknown":
            return sid
        return "unknown_scene"

    def normalized_scene_id(self) -> str:
        hint = (self.legacy_scene_hint or "").strip().lower()
        if hint in VALID_SCENE_IDS:
            return hint
        slug = self.normalized_slug()
        if slug != "unknown_scene":
            return legacy_scene_hint_from_slug(slug)
        sid = (self.scene_id or "unknown").strip().lower()
        if sid in VALID_SCENE_IDS:
            return sid
        return "unknown"

    def normalized_action(self) -> str:
        act = (self.action or "none").strip().lower()
        if act in VALID_SCENE_ACTIONS:
            return act
        return "none"

    def normalized_coord_strategy(self) -> str:
        cs = (self.coord_strategy or "none").strip().lower()
        if cs not in ("ocr", "pulse", "dim_region", "wait", "vlm_semantic", "none"):
            if self.use_dim_region_tap:
                return "dim_region"
            act = self.normalized_action()
            if act == "wait":
                return "wait"
            if act in ("tap_dialogue", "tap_skip", "tap_continue"):
                return "ocr"
            return "none"
        return cs

    def needs_ocr_tap(self) -> bool:
        if self.normalized_coord_strategy() == "pulse":
            return False
        return self.normalized_action() in ("tap_dialogue", "tap_skip", "tap_continue")

    def to_scene_label_judgment(self) -> SceneLabelJudgment:
        return SceneLabelJudgment(
            label_slug=self.normalized_slug(),
            label_display=self.label_display or self.description[:200],
            confidence=self.confidence,
            coord_strategy=self.normalized_coord_strategy(),  # type: ignore[arg-type]
            semantic_target=self.semantic_target,
            match_prior_label_id=self.match_prior_label_id,
            description=self.description,
            reason=self.reason,
            use_dim_region_tap=self.use_dim_region_tap,
            dim_region_hint=self.dim_region_hint,
            legacy_scene_hint=self.legacy_scene_hint or self.normalized_scene_id(),
        )
