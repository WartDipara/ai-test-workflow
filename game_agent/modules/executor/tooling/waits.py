from __future__ import annotations

import asyncio
import logging

from pydantic_ai import RunContext

from game_agent.modules.executor.deps import ExecutorAgentDeps
from game_agent.services.game_entry_check import run_in_game_check
from game_agent.services.game_launch import is_game_running, mark_game_process_detected
from game_agent.services.package_install import (
    PackageWaitResult,
    wait_for_package_installed as poll_package_installed,
)
from game_agent.services.polling import poll_until_async

logger = logging.getLogger(__name__)


def _should_abort(ctx: RunContext[ExecutorAgentDeps]) -> bool:
    actx = ctx.deps.attempt_context
    return actx is not None and actx.should_stop_executor()


async def execute_wait_for_package(
    ctx: RunContext[ExecutorAgentDeps],
    timeout_s: float | None,
) -> str:
    cfg = ctx.deps.app_config
    pkg = cfg.game.package_name
    timeout = (
        float(timeout_s)
        if timeout_s is not None
        else cfg.game.package_install_wait_timeout_s
    )
    interval = cfg.game.package_install_poll_interval_s

    result: PackageWaitResult = await asyncio.to_thread(
        poll_package_installed,
        ctx.deps.adb,
        pkg,
        timeout_s=timeout,
        poll_interval_s=interval,
        should_abort=lambda: _should_abort(ctx),
    )
    out = result.to_tool_message()
    if result.ok:
        ctx.deps.run_state.package_install_confirmed = True
    elif result.aborted:
        actx = ctx.deps.attempt_context
        if actx is not None:
            out = actx.get_fatal_reason() or out
    else:
        ctx.deps.run_state.finished = True
        ctx.deps.run_state.success = False
        ctx.deps.run_state.note = out[:2000]
    return out


async def execute_wait_for_game_running(
    ctx: RunContext[ExecutorAgentDeps],
    summary: str,
    timeout_s: float | None,
) -> str:
    cfg = ctx.deps.app_config
    game_pkg = cfg.game.package_name
    run = ctx.deps.run_state
    run.launch_wait_invoked = True

    timeout = (
        float(timeout_s)
        if timeout_s is not None
        else cfg.game.launch_detect_timeout_s
    )
    timeout = max(15.0, min(timeout, 600.0))
    interval = cfg.game.launch_detect_poll_interval_s
    note = (summary or "Completed login/launch actions").strip()[:2000]
    run.note = note

    logger.info(
        "[Executor] wait_for_game_running %s | timeout %.0fs | %s",
        game_pkg,
        timeout,
        note[:120],
    )
    if ctx.deps.audit is not None:
        ctx.deps.audit.log_phase(
            "executor",
            "开始等待游戏进程",
            package=game_pkg,
            timeout_s=timeout,
            summary=note[:500],
        )

    def predicate() -> bool:
        return is_game_running(ctx.deps.adb, game_pkg)

    outcome = await poll_until_async(
        predicate=predicate,
        timeout_s=timeout,
        interval_s=interval,
        should_abort=lambda: _should_abort(ctx),
        log_prefix="GameProcess",
    )

    if outcome.ok:
        mark_game_process_detected(
            run,
            game_package=game_pkg,
            reason=note or f"Game process detected after {outcome.polls} poll(s)",
        )
        return (
            f"[wait_for_game_running] Game process {game_pkg} detected "
            f"(poll #{outcome.polls}). Proceed with login; use check_in_game after "
            "server_select/download/HUD."
        )

    if outcome.aborted:
        actx = ctx.deps.attempt_context
        reason = (actx.get_fatal_reason() if actx else None) or "parallel monitor stop"
        return f"[wait_for_game_running] Aborted: {reason}"

    run.finished = True
    run.success = False
    fail_msg = (
        f"[wait_for_game_running] Timeout: process {game_pkg} not detected within "
        f"{timeout:.0f}s ({outcome.polls} polls). Context: {note}. "
        "Call report_flow_done(success=false) or review login steps."
    )
    run.note = fail_msg[:2000]
    logger.warning("[Executor] %s", fail_msg)
    if ctx.deps.audit is not None:
        ctx.deps.audit.log_phase(
            "executor",
            "等待游戏进程超时",
            package=game_pkg,
            timeout_s=timeout,
        )
    return fail_msg


async def execute_check_in_game(ctx: RunContext[ExecutorAgentDeps]) -> str:
    result = await run_in_game_check(
        adb=ctx.deps.adb,
        cfg=ctx.deps.app_config,
        run_state=ctx.deps.run_state,
        artifact_root=ctx.deps.artifact_root,
        audit=ctx.deps.audit,
        round_id=ctx.deps.round_id,
        sessions_restarted=0,
    )
    return result.message
