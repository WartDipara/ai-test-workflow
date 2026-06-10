from __future__ import annotations

from pathlib import Path

from game_agent.models.run_failure import RunFailure


class RunTerminalError(RuntimeError):
    """Non-retryable failure; orchestrator must exit without further retries."""

    def __init__(self, failure: RunFailure) -> None:
        self.failure = failure
        super().__init__(failure.format())


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


class ConfigPatchGenerationError(RuntimeError):
    """Modify 阶段核心失败：域名分析或配置补丁流程无法继续。"""

    def __init__(self, message: str, *, stage: str = "modify") -> None:
        super().__init__(message)
        self.stage = stage


class ConfigPatchLlmError(ConfigPatchGenerationError):
    """Modify 阶段 LLM 请求/解析失败（与 AI 业务判断「无可改」区分）。"""

    def __init__(
        self,
        message: str,
        *,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> None:
        super().__init__(message, stage="llm_patch")
        self.attempt = attempt
        self.max_attempts = max_attempts


class ConfigPatchRejectedError(ConfigPatchGenerationError):
    """AI 请求成功，但明确判断当前无可安全修改的 direct_patterns/port_rules。"""

    def __init__(self, message: str, *, analysis: str = "", stage: str = "ai_rejected") -> None:
        super().__init__(message, stage=stage)
        self.analysis = analysis
