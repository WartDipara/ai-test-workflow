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


_RETRYABLE_PREFIXES = (
    "log anomaly detected:",
    "screen anomaly detected:",
    "network anomaly confirmed",
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
    "缺少 deploy 合并配置",
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

    for prefix in _RETRYABLE_PREFIXES:
        if not lower.startswith(prefix):
            continue
        if prefix.startswith("network anomaly confirmed"):
            code = ErrorCode.NET_LOG_ANOMALY
        elif "log anomaly" in prefix:
            code = ErrorCode.NET_LOG_ANOMALY
        else:
            code = ErrorCode.NET_SCREEN_ANOMALY
        return RunFailure(code, text, retryable=True)

    if any(s in lower for s in _NON_RETRYABLE_SUBSTRINGS):
        code = ErrorCode.EXECUTOR_FLOW
        if "预处理" in text:
            code = ErrorCode.PREPROCESS
        elif "deploy" in lower and "失败" in text:
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
        if "network anomaly confirmed" in lower:
            code = ErrorCode.NET_LOG_ANOMALY
        elif "log anomaly" in lower:
            code = ErrorCode.NET_LOG_ANOMALY
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
        return RunFailure(ErrorCode.CONFIG, text, retryable=False)

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
        return RunFailure(ErrorCode.INTERNAL, "Interrupted", retryable=False, detail=msg)

    from game_agent.services.shutdown import ShutdownRequested

    if isinstance(exc, ShutdownRequested):
        return RunFailure(
            ErrorCode.INTERNAL,
            f"Interrupted: {exc.reason}",
            retryable=False,
            detail=msg,
        )

    from game_agent.exceptions import DeployPhaseError

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
