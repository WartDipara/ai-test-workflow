"""Scene label registry 配置。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SceneLabelsSection(BaseModel):
    enabled: bool = Field(True, description="启用 VLM 动态场景标记与注册表复用。")
    min_retrieve_similarity: float = Field(
        0.35,
        ge=0.1,
        le=1.0,
        description="指纹相似度阈值，命中则快路径。",
    )
    min_learn_confidence: float = Field(
        0.45,
        ge=0.1,
        le=1.0,
        description="写入注册表的最低置信度。",
    )
    max_known_labels_in_prompt: int = Field(
        20,
        ge=0,
        le=50,
        description="注入 VLM prompt 的已知 label 数量上限。",
    )
    bootstrap_legacy_archetypes: bool = Field(
        True,
        description="空注册表时写入 dialogue/tutorial 等种子 label。",
    )
