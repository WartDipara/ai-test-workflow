from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GameTurboConfigPatch(BaseModel):
    """AI 允许提出的 GameTurbo 游戏配置补丁（Modify 阶段）。"""

    analysis: str = Field(
        "",
        description="异常根因与本次修改理由；须说明依据 domain_region_analysis 的哪些字段。",
    )
    port_rules: list[dict[str, Any]] = Field(
        default_factory=list,
        description="可选：按 port 合并/覆盖的端口规则；仅在域名分析无法表达时少量使用。",
    )
    direct_patterns: list[str] = Field(
        default_factory=list,
        description="可选：追加到 direct_patterns 的域名/后缀；仅确信为资源/CDN/下载类域名。",
    )

