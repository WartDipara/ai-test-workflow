from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Unified run failure codes. E1xxx = do not retry; E2xxx = may retry (network/acceleration)."""

    OK = "E0000"
    # --- E1xxx: infrastructure, code, environment (non-retryable) ---
    INTERNAL = "E1000"
    EXECUTOR_RUNTIME = "E1001"
    LLM_AUTH = "E1002"
    LLM_API = "E1003"
    DEPLOY_INFRA = "E1004"
    PREPROCESS = "E1005"
    CONFIG = "E1006"
    VISION_PROBE = "E1007"
    EXECUTOR_FLOW = "E1008"
    PACKAGE_INSTALL = "E1009"
    TIMEOUT_PHASE = "E1010"
    NET_TUNNEL_IDLE = "E1011"  # tunnel up but zero SNI — not effective, not retryable

    # --- Retryable: GameTurbo / network acceleration / routing ---
    NET_LOG_ANOMALY = "E2001"
    NET_SCREEN_ANOMALY = "E2002"
    NET_SESSION_LIMIT = "E2003"
    NET_ROUTING = "E2004"
    NET_DOWNLOAD = "E2005"
    EXECUTOR_NETWORK = "E2006"
    FOREGROUND_LOST = "E2007"


@dataclass(frozen=True, slots=True)
class RunFailure:
    code: ErrorCode
    message: str
    retryable: bool
    detail: str = ""

    def format(self) -> str:
        msg = self.message.strip()
        prefix = f"[{self.code.value}]"
        if msg.startswith(prefix):
            base = msg
        else:
            base = f"{prefix} {msg}"
        if self.detail:
            return f"{base} | {self.detail[:1500]}"
        return base

    def to_note(self) -> str:
        return self.format()[:4000]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "retryable": self.retryable,
            "message": self.message,
            "detail": self.detail[:2000],
        }


USER_INTERRUPT_MESSAGE = "User interrupted"
USER_INTERRUPT_DETAIL = "Batch run or task stopped by user"


def user_interrupt_failure() -> RunFailure:
    """用户 Ctrl+C / 批跑停止请求：固定不可重试失败，不触发 AI 排障。"""
    return RunFailure(
        ErrorCode.INTERNAL,
        USER_INTERRUPT_MESSAGE,
        retryable=False,
        detail=USER_INTERRUPT_DETAIL,
    )


def is_user_interrupt_requested() -> bool:
    from game_agent.services.shutdown import is_shutdown_requested

    return is_shutdown_requested()


_RETRYABLE_PREFIXES = (
    "vision/ocr network anomaly confirmed",
    "observer network anomaly confirmed",
    "screen anomaly detected:",
)

_RETRYABLE_SUBSTRINGS = (
    "tunnel closed",
    "channel closed",
    "network failed",
    "no network",
    "connection timeout",
    "connection failed",
    "server connection",
    "download failed",
    "resource download",
    "update failed",
    "server busy",
    "server maintenance",
    "session restart limit",
    "sni-tunnel",
    "routing",
    "direct_patterns",
    "domain_region",
)

_MODIFY_CORE_FAIL_SUBSTRINGS = (
    "domain_region_analysis",
    "ai 分析认为当前日志/域名下无可安全追加",
    "ai found no safe config changes",
    "modify 阶段 ai 判断无可修改配置",
    "modify stage: no safe config change",
    "配置补丁应用后无任何变更",
    "config patch applied with no changes",
    "modify 阶段 ai 请求失败",
    "modify stage ai request failed",
    "ai 配置补丁请求失败",
    "ai config patch request failed",
    "ai 未返回结构化",
    "缺少 gameturbo.log",
    "missing valid gameturbo.log",
    "域名/区域分析失败",
    "domain/region analysis failed",
    "modify 阶段核心失败",
    "modify stage core failure",
    "config patch generation",
)

_NON_RETRYABLE_SUBSTRINGS = (
    "is not defined",
    "nameerror",
    "syntaxerror",
    "typeerror",
    "attributeerror",
    "importerror",
    "traceback (most recent",
    "agent.run",
    "authenticationerror",
    "status_code: 401",
    "multimodal probe failed",
    "预处理失败",
    "preprocess failed",
    "缺少 deploy 合并配置",
    "missing deploy merge config",
    "executor ended:",
    "rounds without",
    "check_in_game confirmation",
    "executor stopped without",
    "executor failed",
    "in-game not confirmed",
    "package install",
    "timeout: package",
    "config error:",
    "credentials",
    "ocr failed",
    "paddleocr",
)


def _lower(reason: str) -> str:
    return (reason or "").lower()


_GAMETURBO_PREPARE_INFRA_MARKERS = (
    "winerror",
    "filenotfound",
    "errno",
    "asyncio.run()",
    "deploy.sh",
    "bash",
    "系统找不到指定的文件",
    "no such file",
    "cannot find",
)


def _gameturbo_prepare_failure_code(lower: str) -> ErrorCode:
    """GameTurbo init/prepare failures: infra vs true config issues."""
    if any(marker in lower for marker in _GAMETURBO_PREPARE_INFRA_MARKERS):
        return ErrorCode.DEPLOY_INFRA
    if "deploy" in lower and ("失败" in lower or "failed" in lower):
        return ErrorCode.DEPLOY_INFRA
    return ErrorCode.CONFIG


def compact_failure_message(message: str, *, max_len: int = 2000) -> str:
    """截断失败文案时保留错误码与 ServerCheck 摘要行，避免 probe 前缀挤掉 [E2006]。"""
    text = (message or "").strip()
    if len(text) <= max_len:
        return text
    code = ""
    for token in ("E2006", "E2002", "E1000", "E1001"):
        if f"[{token}]" in text:
            code = token
            break
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if "[ServerCheck]" in stripped or (code and f"[{code}]" in stripped):
            summary = stripped
            if code and not summary.startswith(f"[{code}]"):
                summary = f"[{code}] {stripped}"
            return summary[:max_len]
    if code:
        tail_budget = max(0, max_len - len(code) - 6)
        return f"[{code}] ..." + text[-tail_budget:]
    return text[:max_len]


def classify_failure(
    reason: str,
    *,
    exc: BaseException | None = None,
) -> RunFailure:
    """
    Map a failure reason (and optional exception) to a RunFailure with retry policy.

    Default when ambiguous: **non-retryable** (avoid useless retries on code bugs).
    """
    if exc is not None:
        return classify_exception(exc, context=reason)

    text = (reason or "").strip()
    existing = parse_error_code_from_text(text)
    if existing:
        try:
            ec = ErrorCode(existing)
        except ValueError:
            ec = ErrorCode.INTERNAL
        return RunFailure(ec, text, retryable=existing.startswith("E2"))

    lower = _lower(text)
    if not text:
        return RunFailure(
            ErrorCode.INTERNAL,
            "Empty failure reason",
            retryable=False,
        )

    if "asyncio.run() cannot be called from a running event loop" in lower:
        return RunFailure(
            ErrorCode.DEPLOY_INFRA,
            text,
            retryable=False,
            detail="Nested asyncio.run in deploy/plugin bridge",
        )

    if lower.startswith("log anomaly detected:"):
        return RunFailure(
            ErrorCode.INTERNAL,
            text,
            retryable=False,
            detail="Runtime log rule analysis disabled; logs are collect-only during play",
        )

    if "vision/ocr network anomaly confirmed" in lower:
        return RunFailure(ErrorCode.NET_SCREEN_ANOMALY, text, retryable=True)

    if "前台应用丢失" in text or "foreground app lost" in lower or "foreground recover failed" in lower:
        return RunFailure(ErrorCode.FOREGROUND_LOST, text, retryable=True)

    if "observer network anomaly confirmed" in lower:
        return RunFailure(ErrorCode.NET_SCREEN_ANOMALY, text, retryable=True)

    for prefix in _RETRYABLE_PREFIXES:
        if not lower.startswith(prefix):
            continue
        if prefix.startswith("observer network anomaly confirmed"):
            code = ErrorCode.NET_SCREEN_ANOMALY
        elif prefix.startswith("vision/ocr network anomaly confirmed"):
            code = ErrorCode.NET_SCREEN_ANOMALY
        else:
            code = ErrorCode.NET_SCREEN_ANOMALY
        return RunFailure(code, text, retryable=True)

    if any(s in lower for s in _MODIFY_CORE_FAIL_SUBSTRINGS):
        return RunFailure(
            ErrorCode.CONFIG,
            text,
            retryable=False,
            detail="Modify stage core failure (domain analysis or config patch)",
        )

    if any(s in lower for s in _NON_RETRYABLE_SUBSTRINGS):
        code = ErrorCode.EXECUTOR_FLOW
        if "预处理" in text or "preprocess failed" in lower:
            code = ErrorCode.PREPROCESS
        elif "deploy" in lower and ("失败" in text or "failed" in lower):
            code = ErrorCode.DEPLOY_INFRA
        elif "401" in lower or "authentication" in lower:
            code = ErrorCode.LLM_AUTH
        elif "multimodal probe" in lower:
            code = ErrorCode.VISION_PROBE
        elif "package" in lower and (
            "timeout" in lower
            or "not installed" in lower
            or "pm path" in lower
            or "not on device" in lower
        ):
            # E1009：deploy/安装基础设施失败，非 E2 网络加速类，不可重试
            return RunFailure(ErrorCode.PACKAGE_INSTALL, text, retryable=False)
        elif "parallel game phase timeout" in lower:
            code = ErrorCode.TIMEOUT_PHASE
        elif "is not defined" in lower or "nameerror" in lower:
            code = ErrorCode.EXECUTOR_RUNTIME
        return RunFailure(code, text, retryable=False)

    if any(s in lower for s in _RETRYABLE_SUBSTRINGS):
        code = ErrorCode.NET_ROUTING
        if "vision/ocr network anomaly confirmed" in lower:
            code = ErrorCode.NET_SCREEN_ANOMALY
        elif "network anomaly confirmed" in lower:
            code = ErrorCode.NET_SCREEN_ANOMALY
        elif "screen anomaly" in lower:
            code = ErrorCode.NET_SCREEN_ANOMALY
        elif "session restart" in lower:
            code = ErrorCode.NET_SESSION_LIMIT
        elif "download" in lower:
            code = ErrorCode.NET_DOWNLOAD
        return RunFailure(code, text, retryable=True)

    if "deploy" in lower and ("失败" in text or "failed" in lower):
        return RunFailure(ErrorCode.DEPLOY_INFRA, text, retryable=False)

    if "gameturbo" in lower and "前置" in text:
        code = _gameturbo_prepare_failure_code(lower)
        return RunFailure(code, text, retryable=False)

    if "no sni traffic" in lower:
        return RunFailure(ErrorCode.NET_TUNNEL_IDLE, text, retryable=False)

    return RunFailure(
        ErrorCode.INTERNAL,
        text,
        retryable=False,
        detail="Unclassified failure; treated as non-retryable",
    )


def classify_exception(
    exc: BaseException,
    *,
    context: str = "",
) -> RunFailure:
    msg = str(exc).strip() or exc.__class__.__name__
    combined = f"{context} {msg}".strip()

    if isinstance(exc, KeyboardInterrupt):
        return user_interrupt_failure()

    from game_agent.services.shutdown import ShutdownRequested

    if isinstance(exc, ShutdownRequested):
        return user_interrupt_failure()

    from game_agent.exceptions import (
        ConfigPatchGenerationError,
        ConfigPatchLlmError,
        ConfigPatchRejectedError,
        DeployPhaseError,
    )

    if isinstance(exc, ConfigPatchLlmError):
        return RunFailure(
            ErrorCode.LLM_API,
            "Modify stage AI request failed",
            retryable=False,
            detail=(
                f"stage=llm_patch; attempts={exc.attempt}/{exc.max_attempts}; "
                f"{msg[:400]}; {context[:200]}"
            ),
        )

    if isinstance(exc, ConfigPatchRejectedError):
        analysis = exc.analysis[:800] if exc.analysis else ""
        return RunFailure(
            ErrorCode.CONFIG,
            "Modify stage: no safe config change",
            retryable=False,
            detail=f"stage={exc.stage}; {analysis or msg[:400]}",
        )

    if isinstance(exc, ConfigPatchGenerationError):
        return RunFailure(
            ErrorCode.CONFIG,
            msg,
            retryable=False,
            detail=f"stage={exc.stage}; {context[:400]}",
        )

    if isinstance(exc, DeployPhaseError):
        failure = classify_failure(msg)
        if failure.retryable:
            return failure
        return RunFailure(
            ErrorCode.DEPLOY_INFRA,
            msg,
            retryable=False,
            detail=context[:500],
        )

    if isinstance(exc, (NameError, SyntaxError, TypeError, AttributeError, ImportError)):
        return RunFailure(
            ErrorCode.EXECUTOR_RUNTIME,
            f"{exc.__class__.__name__}: {msg}",
            retryable=False,
            detail=context[:500],
        )

    if isinstance(exc, RuntimeError) and (
        "asyncio.run() cannot be called from a running event loop" in msg.lower()
    ):
        return RunFailure(
            ErrorCode.DEPLOY_INFRA,
            msg,
            retryable=False,
            detail="Nested asyncio.run in deploy/plugin bridge",
        )

    lower = _lower(combined)
    if "401" in lower or "authentication" in lower:
        return RunFailure(ErrorCode.LLM_AUTH, msg, retryable=False, detail=context[:500])

    if re.search(r"status_code:\s*4\d\d", lower) and "tool_choice" in lower:
        return RunFailure(ErrorCode.LLM_API, msg, retryable=False, detail=context[:500])

    routed = classify_failure(combined)
    if routed.code != ErrorCode.INTERNAL:
        return routed

    return RunFailure(
        ErrorCode.INTERNAL,
        f"{exc.__class__.__name__}: {msg}",
        retryable=False,
        detail=context[:500],
    )


_ERROR_CODE_RE = re.compile(r"^\[(E\d{4})\]")


def parse_error_code_from_text(text: str) -> str:
    m = _ERROR_CODE_RE.match((text or "").strip())
    return m.group(1) if m else ""
