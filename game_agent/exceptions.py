from __future__ import annotations

from pathlib import Path


class DeployPhaseError(RuntimeError):
    """deploy.sh 在 AI 辅助重试后仍失败。"""

    def __init__(
        self,
        message: str,
        *,
        log_path: Path | None = None,
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.log_path = log_path
        self.attempts = attempts
