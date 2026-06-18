"""资源下载 gate 裁决结果。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DownloadGateJudgment(BaseModel):
    is_download: bool = False
    in_progress: bool = False
    progress_text: str = ""
    action: str = Field(default="wait", description="wait | tap_continue | done")
    tap_x: int = 0
    tap_y: int = 0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
