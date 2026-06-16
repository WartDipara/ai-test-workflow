from __future__ import annotations

from pydantic import BaseModel, Field


class ServerPanelVisionVerdict(BaseModel):
    passed: bool = False
    same_screen: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    parse_failed: bool = False
