from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

_PIPELINE_STAGE: ContextVar[str] = ContextVar("pipeline_stage", default="-")

STAGE_LOG_FORMAT = (
    "%(asctime)s [%(pipeline_stage)s] %(levelname)s %(name)s: %(message)s"
)

_STAGE_FILTER = None  # singleton PipelineStageFilter


class PipelineStageFilter(logging.Filter):
    """Inject current pipeline stage into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.pipeline_stage = get_pipeline_stage()
        return True


def get_pipeline_stage() -> str:
    return _PIPELINE_STAGE.get()


def bind_pipeline_stage(phase: str) -> Token[str]:
    return _PIPELINE_STAGE.set(phase)


def reset_pipeline_stage(token: Token[str]) -> None:
    _PIPELINE_STAGE.reset(token)


def _stage_filter() -> PipelineStageFilter:
    global _STAGE_FILTER
    if _STAGE_FILTER is None:
        _STAGE_FILTER = PipelineStageFilter()
    return _STAGE_FILTER


def attach_stage_formatter(handler: logging.Handler, level: str = "INFO") -> None:
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler.setFormatter(logging.Formatter(STAGE_LOG_FORMAT))
    if not any(isinstance(f, PipelineStageFilter) for f in handler.filters):
        handler.addFilter(_stage_filter())


def ensure_stage_filter_on_handlers(root: logging.Logger | None = None) -> None:
    root = root or logging.getLogger()
    stage_filter = _stage_filter()
    if not any(isinstance(f, PipelineStageFilter) for f in root.filters):
        root.addFilter(stage_filter)
    for handler in root.handlers:
        if not any(isinstance(f, PipelineStageFilter) for f in handler.filters):
            handler.addFilter(stage_filter)
        if isinstance(handler.formatter, logging.Formatter):
            fmt = handler.formatter._fmt if hasattr(handler.formatter, "_fmt") else ""
            if fmt and "pipeline_stage" not in fmt:
                handler.setFormatter(logging.Formatter(STAGE_LOG_FORMAT))


def install_stage_aware_logging(level: str = "INFO", *, force: bool = False) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    if force or not logging.getLogger().handlers:
        logging.basicConfig(
            level=log_level,
            format=STAGE_LOG_FORMAT,
            force=True,
        )
    else:
        logging.getLogger().setLevel(log_level)
    ensure_stage_filter_on_handlers()


@contextmanager
def pipeline_stage(
    phase: str,
    *,
    gameturbo_root: Path | None = None,
    note: str = "",
    **_: Any,
) -> Iterator[None]:
    """
    Bind pipeline stage for logging in the current thread/task.
    Optionally write a gameturbo.log stage separator when gameturbo_root is set.
    """
    if gameturbo_root is not None:
        from game_agent.services.gameturbo_log import append_gameturbo_stage_marker

        append_gameturbo_stage_marker(gameturbo_root, phase, note)

    token = bind_pipeline_stage(phase)
    try:
        yield
    finally:
        reset_pipeline_stage(token)
