from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DeployRecoveryPatch(BaseModel):
    """deploy.sh 失败时 AI 给出的恢复建议（Init / Modify 阶段）。"""

    analysis: str = Field("", description="根因与本次采取的动作说明。")
    direct_patterns: list[str] = Field(default_factory=list)
    port_rules: list[dict[str, Any]] = Field(default_factory=list)
    game_id: str | None = Field(
        None,
        description="若配置中 game_id 与目标 gid 不一致，可建议修正为正确 gid。",
    )
    retry_only: bool = Field(
        False,
        description="为 true 时不改配置，仅建议立即重试 deploy（如瞬时构建/网络问题）。",
    )
