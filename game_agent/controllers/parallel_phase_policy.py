"""并行游戏阶段超时/成功判定策略（可单测）。"""

from __future__ import annotations


def should_signal_parallel_timeout_fatal(
    *,
    timed_out: bool,
    phase_ok: bool,
    in_game_signaled: bool,
    executor_in_game_confirmed: bool | None,
) -> bool:
    """
    是否应对并行阶段发出 timeout fatal。

    已成功（phase_ok / 成功信号 / executor 已确认）时不得因 timeout 判失败。
    """
    if phase_ok or in_game_signaled:
        return False
    if executor_in_game_confirmed:
        return False
    return timed_out


def should_return_parallel_timeout_failure(
    *,
    timed_out: bool,
    phase_ok: bool,
    in_game_signaled: bool,
    executor_in_game_confirmed: bool | None,
    executor_enabled: bool,
) -> bool:
    """是否向 orchestrator 返回 parallel timeout 失败文案。"""
    if not executor_enabled:
        return False
    return should_signal_parallel_timeout_fatal(
        timed_out=timed_out,
        phase_ok=phase_ok,
        in_game_signaled=in_game_signaled,
        executor_in_game_confirmed=executor_in_game_confirmed,
    )
