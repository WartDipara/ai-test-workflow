"""进程闪退/重启时作废进行中的 executor 工作（含 VLM API）。"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game_agent.modules.run_context import AttemptContext

logger = logging.getLogger(__name__)

_bound_attempt_context: ContextVar[AttemptContext | None] = ContextVar(
    "bound_attempt_context",
    default=None,
)


@contextmanager
def bind_executor_attempt_context(ctx: AttemptContext | None):
    """LangGraph 执行线程内绑定 AttemptContext，供 VisionWorker 读取世代号。"""
    token = _bound_attempt_context.set(ctx)
    try:
        yield
    finally:
        _bound_attempt_context.reset(token)


def get_bound_attempt_context() -> AttemptContext | None:
    return _bound_attempt_context.get()


def capture_session_generation(ctx: AttemptContext | None = None) -> int:
    ac = ctx or get_bound_attempt_context()
    if ac is None:
        return 0
    return ac.get_session_generation()


def is_stale_generation(captured: int, ctx: AttemptContext | None = None) -> bool:
    ac = ctx or get_bound_attempt_context()
    if ac is None:
        return False
    return ac.is_session_generation_stale(captured)


def discard_if_stale(
    captured: int,
    *,
    where: str,
    ctx: AttemptContext | None = None,
) -> bool:
    """若世代已过期返回 True（调用方应丢弃结果）。"""
    if not is_stale_generation(captured, ctx):
        return False
    ac = ctx or get_bound_attempt_context()
    current = ac.get_session_generation() if ac is not None else -1
    logger.warning(
        "[SessionInvalidation] discard stale work at %s captured_gen=%d current_gen=%d",
        where,
        captured,
        current,
    )
    return True


def guard_session_work(
    state: dict,
    *,
    ctx: AttemptContext | None = None,
    where: str = "",
) -> bool:
    """True = 会话已失效，调用方应跳过 action / 不 merge VLM 结果。"""
    gen = int(state.get("session_work_generation") or 0)
    if discard_if_stale(gen, where=where or "action", ctx=ctx):
        return True
    ac = ctx or get_bound_attempt_context()
    if ac is not None and ac.is_session_invalidated():
        return True
    return False
