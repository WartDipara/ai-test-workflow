"""动态阶段模板：AI 填表，引擎执行；不含游戏业务枚举。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ActionKind = Literal["tap_xy", "wait", "press_back", "none"]
CompletionKind = Literal[
    "fingerprint_change",
    "ocr_contains",
    "always_after_wait",
    "manual_next_plan",
]


class CompletionRule(BaseModel):
    kind: CompletionKind = "fingerprint_change"
    hint: str = ""

    def evaluate(
        self,
        *,
        entry_fingerprint: str,
        after_fingerprint: str,
        ocr_summary: str,
        action: ActionKind,
        action_executed: bool,
    ) -> bool:
        if self.kind == "fingerprint_change":
            return bool(
                entry_fingerprint
                and after_fingerprint
                and entry_fingerprint != after_fingerprint
            )
        if self.kind == "ocr_contains":
            hint = (self.hint or "").strip()
            if not hint:
                return False
            return hint in (ocr_summary or "")
        if self.kind == "always_after_wait":
            return action == "wait" and action_executed
        if self.kind == "manual_next_plan":
            return action_executed and (
                not entry_fingerprint
                or entry_fingerprint != after_fingerprint
            )
        return False


class PhaseSpec(BaseModel):
    flow_active: bool = True
    phase_id: str = ""
    phase_label: str = ""
    action: ActionKind = "none"
    x: int = 0
    y: int = 0
    wait_s: float = Field(default=2.0, ge=0.5, le=8.0)
    target_text: str = ""
    reason: str = ""
    complete: CompletionRule = Field(default_factory=CompletionRule)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("phase_id", mode="before")
    @classmethod
    def _normalize_phase_id(cls, value: object) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            return "phase"
        slug = re.sub(r"[^a-z0-9_]+", "_", raw.replace(" ", "_"))
        slug = slug.strip("_")[:48] or "phase"
        return slug

    def node_id(self) -> str:
        return f"adaptive.{self.phase_id}"

    def signature(self) -> str:
        return (
            f"{self.action}:{self.x}:{self.y}:{self.wait_s:.1f}:"
            f"{self.target_text}:{self.complete.kind}:{self.complete.hint}"
        )


class PhaseRecord(BaseModel):
    node_id: str
    phase_id: str
    phase_label: str
    done: bool = False
    attempts: int = 0
    artifact: str = ""
    evidence: str = ""


AdaptiveTreeNodeStatus = Literal["planned", "active", "done", "failed", "skipped"]


class AdaptiveTreeNode(BaseModel):
    """post_login.adaptive 下运行时物化的动态子节点。"""

    node_id: str
    phase_id: str
    phase_label: str
    spec: PhaseSpec
    status: AdaptiveTreeNodeStatus = "active"
    entry_fingerprint: str = ""
    attempts: int = 0
    artifact: str = ""
    evidence: str = ""
    created_round: int = 0


def compute_phase_fingerprint(*, ocr_summary: str, phase_label: str = "") -> str:
    head = (ocr_summary or "")[:320]
    return f"{phase_label}|{head}"
