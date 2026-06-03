from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, TypeVar

from pydantic_ai import Agent, RunContext

from game_agent.modules.executor.deps import ExecutorAgentDeps
from game_agent.services.polling import CALLBACK_HINT

F = TypeVar("F", bound=Callable[..., Awaitable[str]])


class ToolKind(str, Enum):
    """Executor tool contract (see executor_system.en.txt)."""

    INSTANT = "instant"
    WAIT = "wait"
    COMPOUND = "compound"
    TERMINAL = "terminal"


class RunRequirement(str, Enum):
    PACKAGE_INSTALLED = "package_install_confirmed"


_REQUIREMENT_MESSAGES: dict[RunRequirement, str] = {
    RunRequirement.PACKAGE_INSTALLED: (
        "Refused: call wait_for_package_installed once first (internal poll; "
        "tool return continues you automatically)."
    ),
}


def block_if_stopped(ctx: RunContext[ExecutorAgentDeps]) -> str | None:
    if ctx.deps.run_state.in_game_confirmed:
        return "In-game already confirmed; stop calling tools."
    actx = ctx.deps.attempt_context
    if actx is not None and actx.should_stop_executor():
        reason = actx.get_fatal_reason() or (
            "parallel phase stop (game timeout or log monitor — check orchestrator logs)"
        )
        return f"Executor stopped: {reason}"
    return None


def _requirement_met(ctx: RunContext[ExecutorAgentDeps], req: RunRequirement) -> bool:
    if req == RunRequirement.PACKAGE_INSTALLED:
        return bool(ctx.deps.run_state.package_install_confirmed)
    return True


def _log_tool(
    ctx: RunContext[ExecutorAgentDeps],
    name: str,
    args: Any,
    result: str,
) -> None:
    if ctx.deps.audit is not None:
        ctx.deps.audit.log_tool("executor", ctx.deps.round_id, name, args, result)


def _emit(
    ctx: RunContext[ExecutorAgentDeps],
    name: str,
    args: dict[str, Any],
    result: str,
    *,
    view_limit: int = 1200,
    audit_limit: int = 4000,
) -> str:
    ctx.deps.view.tool(name, result[:view_limit])
    _log_tool(ctx, name, args, result[:audit_limit])
    return result


def _wrap_executor_tool(
    fn: F,
    *,
    kind: ToolKind,
    check_stopped: bool,
    requirements: tuple[RunRequirement, ...],
    idempotent_attr: str | None,
    idempotent_message: Callable[[RunContext[ExecutorAgentDeps]], str] | None,
) -> F:
    tool_name = fn.__name__

    @functools.wraps(fn)
    async def wrapper(ctx: RunContext[ExecutorAgentDeps], /, *args: Any, **kwargs: Any) -> str:
        bound = inspect.signature(fn).bind(ctx, *args, **kwargs)
        bound.apply_defaults()
        log_args = {
            k: v
            for k, v in bound.arguments.items()
            if k != "ctx"
        }

        if check_stopped:
            blocked = block_if_stopped(ctx)
            if blocked:
                return _emit(ctx, tool_name, log_args, blocked)

        for req in requirements:
            if not _requirement_met(ctx, req):
                return _emit(ctx, tool_name, log_args, _REQUIREMENT_MESSAGES[req])

        if idempotent_attr and getattr(ctx.deps.run_state, idempotent_attr, False):
            if idempotent_message is not None:
                msg = idempotent_message(ctx)
            else:
                msg = (
                    f"[{tool_name}] Already completed this run ({idempotent_attr}). "
                    "Do not call again."
                )
            return _emit(ctx, tool_name, log_args, msg)

        if kind == ToolKind.WAIT:
            ctx.deps.view.tool(tool_name, f"[{tool_name}] polling…")

        result = await fn(ctx, *args, **kwargs)

        if kind == ToolKind.WAIT and result and CALLBACK_HINT not in result:
            result = f"{result.rstrip()}\n{CALLBACK_HINT}"

        return _emit(ctx, tool_name, log_args, result)

    return wrapper  # type: ignore[return-value]


def make_tool_registrar(agent: Agent[ExecutorAgentDeps, str]) -> Callable[..., Any]:
    """
    Register an executor tool with shared cross-cutting behavior.

    Usage::

        t = make_tool_registrar(agent)

        @t(kind=ToolKind.INSTANT)
        async def tap_coordinate(ctx, x: int, y: int) -> str:
            ...
    """

    def register(
        fn: F | None = None,
        /,
        *,
        kind: ToolKind = ToolKind.INSTANT,
        check_stopped: bool = True,
        requirements: tuple[RunRequirement, ...] = (),
        idempotent_attr: str | None = None,
        idempotent_message: Callable[[RunContext[ExecutorAgentDeps]], str] | None = None,
    ) -> F | Callable[[F], F]:
        def apply(func: F) -> F:
            wrapped = _wrap_executor_tool(
                func,
                kind=kind,
                check_stopped=check_stopped,
                requirements=requirements,
                idempotent_attr=idempotent_attr,
                idempotent_message=idempotent_message,
            )
            agent.tool(wrapped)
            return func

        if fn is not None:
            return apply(fn)
        return apply

    return register
