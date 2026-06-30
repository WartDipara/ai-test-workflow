"""局内会话进展 VLM 判定结果。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class InGameSessionProgressJudgment(BaseModel):
  """动作后 VLM 判断本局内会话是否相对上一轮有可见推进。"""

  session_progressed: bool = Field(
      default=False,
      description="相对动作前，引导/教程/阻塞 UI 是否有可见推进。",
  )
  confidence: float = Field(default=0.0, ge=0.0, le=1.0)
  reason: str = Field(default="", description="一句说明。")
