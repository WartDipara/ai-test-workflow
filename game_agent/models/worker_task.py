from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

WorkerTaskStatus = Literal[
    "pending",
    "running",
    "reporting",
    "completed",
    "failed",
    "timeout",
    "cancelled",
    "stale",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class WorkerProgress:
    status: WorkerTaskStatus
    progress: int
    current_step: str
    message: str = ""
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class WorkerTaskResult:
    report: str
    confidence: float | None = None
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)


@dataclass(slots=True)
class WorkerTaskSnapshot:
    task_id: str
    worker_name: str
    round_id: int
    screenshot_path: Path
    status: WorkerTaskStatus
    progress: int
    current_step: str
    message: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    result: WorkerTaskResult | None = None
    error: str | None = None

    @property
    def is_done(self) -> bool:
        return self.status in {"completed", "failed", "timeout", "cancelled", "stale"}

    def to_worker_report(self) -> str:
        lines = [
            "=== 职员任务状态 ===",
            f"task_id={self.task_id}",
            f"worker={self.worker_name}",
            f"round_id={self.round_id}",
            f"status={self.status}",
            f"progress={self.progress}",
            f"current_step={self.current_step}",
            f"message={self.message or '<none>'}",
            f"updated_at={self.updated_at.isoformat()}",
            f"screenshot={self.screenshot_path}",
        ]
        if self.error:
            lines.append(f"error={self.error}")
        if self.result is not None:
            lines.extend(
                [
                    "",
                    "=== 职员最终报告 ===",
                    self.result.report,
                ],
            )
        return "\n".join(lines)
