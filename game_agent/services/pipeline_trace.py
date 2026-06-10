from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from game_agent.utils.stage_logging import get_pipeline_stage

_pipeline_logger = logging.getLogger("game_agent.pipeline")

_current_tracer: ContextVar[PipelineTracer | None] = ContextVar(
    "pipeline_tracer",
    default=None,
)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass
class PipelineTraceSettings:
    enabled: bool = True
    verbose: bool = True


class PipelineTracer:
    """可开关的流水线调用追踪：记录调用了什么、结果、成功/失败。"""

    def __init__(
        self,
        *,
        artifact_root: Path | None,
        settings: PipelineTraceSettings,
    ) -> None:
        self._settings = settings
        self._artifact_root = artifact_root
        self._jsonl_path: Path | None = None
        self._seq = 0
        if settings.enabled and artifact_root is not None:
            artifact_root.mkdir(parents=True, exist_ok=True)
            self._jsonl_path = artifact_root / "pipeline_trace.jsonl"
            if not self._jsonl_path.is_file():
                self._jsonl_path.write_text("", encoding="utf-8")

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def record(
        self,
        component: str,
        operation: str,
        *,
        status: str,
        message: str = "",
        detail: dict[str, Any] | None = None,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        if not self._settings.enabled:
            return

        self._seq += 1
        payload: dict[str, Any] = {
            "seq": self._seq,
            "ts": _now_iso(),
            "component": component,
            "operation": operation,
            "status": status,
        }
        if message:
            payload["message"] = message[:4000]
        if error:
            payload["error"] = error[:4000]
        if duration_ms is not None:
            payload["duration_ms"] = round(duration_ms, 2)

        if detail:
            if self._settings.verbose:
                payload["detail"] = _sanitize_detail(detail)
            else:
                payload["detail"] = _compact_detail(detail)

        line = json.dumps(payload, ensure_ascii=False, default=str)
        stage = get_pipeline_stage()
        _pipeline_logger.info(
            "[PIPELINE][%s] %s.%s status=%s%s%s%s",
            stage,
            component,
            operation,
            status,
            f" msg={message[:200]}" if message else "",
            f" err={error[:200]}" if error else "",
            f" {duration_ms:.0f}ms" if duration_ms is not None else "",
        )

        if self._jsonl_path is not None:
            try:
                self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                with self._jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                _pipeline_logger.warning(
                    "[PIPELINE] 无法写入 %s: %s（仅保留日志行）",
                    self._jsonl_path,
                    e,
                )
                self._jsonl_path = None

    @contextmanager
    def operation(
        self,
        component: str,
        operation: str,
        **start_detail: Any,
    ) -> Iterator[OperationRecorder]:
        t0 = time.perf_counter()
        self.record(
            component,
            operation,
            status="start",
            detail=start_detail or None,
        )
        recorder = OperationRecorder(self, component, operation, t0)
        try:
            yield recorder
            if not recorder.finished:
                recorder.ok()
        except Exception as exc:
            if not recorder.finished:
                recorder.fail(str(exc))
            raise

    def close(self) -> None:
        if self._settings.enabled:
            self.record("pipeline", "trace_session", status="end")


@dataclass
class OperationRecorder:
    _tracer: PipelineTracer
    _component: str
    _operation: str
    _t0: float
    finished: bool = field(default=False, init=False)

    def ok(self, message: str = "", **detail: Any) -> None:
        if self.finished:
            return
        self.finished = True
        self._tracer.record(
            self._component,
            self._operation,
            status="ok",
            message=message,
            detail=detail or None,
            duration_ms=(time.perf_counter() - self._t0) * 1000,
        )

    def fail(self, error: str, message: str = "", **detail: Any) -> None:
        if self.finished:
            return
        self.finished = True
        self._tracer.record(
            self._component,
            self._operation,
            status="error",
            message=message,
            error=error,
            detail=detail or None,
            duration_ms=(time.perf_counter() - self._t0) * 1000,
        )

    def skip(self, message: str = "", **detail: Any) -> None:
        if self.finished:
            return
        self.finished = True
        self._tracer.record(
            self._component,
            self._operation,
            status="skip",
            message=message,
            detail=detail or None,
            duration_ms=(time.perf_counter() - self._t0) * 1000,
        )


def _sanitize_detail(detail: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in detail.items():
        if isinstance(value, Path):
            out[key] = str(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, list):
            out[key] = value[:50]
        elif isinstance(value, dict):
            out[key] = value
        else:
            out[key] = str(value)[:2000]
    return out


def _compact_detail(detail: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("path", "gid", "returncode", "changed", "domain_count", "output_path"):
        if key in detail:
            compact[key] = detail[key]
    if not compact and detail:
        compact["keys"] = list(detail.keys())[:20]
    return compact


def get_pipeline_tracer() -> PipelineTracer | None:
    return _current_tracer.get()


def activate_pipeline_trace(
    *,
    artifact_root: Path | None,
    enabled: bool,
    verbose: bool,
) -> PipelineTracer:
    tracer = PipelineTracer(
        artifact_root=artifact_root,
        settings=PipelineTraceSettings(enabled=enabled, verbose=verbose),
    )
    _current_tracer.set(tracer)
    if tracer.enabled:
        tracer.record(
            "pipeline",
            "trace_session",
            status="start",
            detail={"artifact_root": str(artifact_root) if artifact_root else None},
        )
    return tracer


def deactivate_pipeline_trace() -> None:
    tracer = _current_tracer.get()
    if tracer is not None:
        tracer.close()
    _current_tracer.set(None)


@contextmanager
def trace_operation(
    component: str,
    operation: str,
    **start_detail: Any,
) -> Iterator[OperationRecorder]:
    """在已 activate 的 tracer 上记录一次操作；未开启时为 no-op。"""
    tracer = get_pipeline_tracer()
    if tracer is None or not tracer.enabled:
        recorder = _NoopRecorder()
        yield recorder
        return
    with tracer.operation(component, operation, **start_detail) as rec:
        yield rec


class _NoopRecorder:
    finished = False

    def ok(self, message: str = "", **detail: Any) -> None:
        self.finished = True

    def fail(self, error: str, message: str = "", **detail: Any) -> None:
        self.finished = True

    def skip(self, message: str = "", **detail: Any) -> None:
        self.finished = True
