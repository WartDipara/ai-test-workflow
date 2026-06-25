"""并行阶段超时策略：launch timeout vs in-game play active。"""

from __future__ import annotations

from game_agent.controllers.parallel_phase_policy import (
    should_return_parallel_timeout_failure,
    should_signal_parallel_timeout_fatal,
)


def test_no_timeout_fatal_when_in_game_play_active() -> None:
    assert not should_signal_parallel_timeout_fatal(
        timed_out=True,
        phase_ok=False,
        in_game_signaled=False,
        executor_in_game_confirmed=False,
        in_game_play_active=True,
    )


def test_timeout_fatal_when_launch_phase_timed_out() -> None:
    assert should_signal_parallel_timeout_fatal(
        timed_out=True,
        phase_ok=False,
        in_game_signaled=False,
        executor_in_game_confirmed=False,
        in_game_play_active=False,
    )


def test_no_return_failure_when_confirmed() -> None:
    assert not should_return_parallel_timeout_failure(
        timed_out=True,
        phase_ok=True,
        in_game_signaled=True,
        executor_in_game_confirmed=True,
        executor_enabled=True,
        in_game_play_active=False,
    )
