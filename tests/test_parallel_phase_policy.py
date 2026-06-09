from game_agent.controllers.parallel_phase_policy import (
    should_return_parallel_timeout_failure,
    should_signal_parallel_timeout_fatal,
)
from game_agent.modules.run_context import AttemptContext


def test_timeout_fatal_when_not_confirmed() -> None:
    assert should_signal_parallel_timeout_fatal(
        timed_out=True,
        phase_ok=False,
        in_game_signaled=False,
        executor_in_game_confirmed=None,
    )


def test_no_timeout_fatal_when_in_game_signaled() -> None:
    assert not should_signal_parallel_timeout_fatal(
        timed_out=True,
        phase_ok=False,
        in_game_signaled=True,
        executor_in_game_confirmed=None,
    )


def test_no_timeout_fatal_when_executor_confirmed() -> None:
    assert not should_signal_parallel_timeout_fatal(
        timed_out=True,
        phase_ok=False,
        in_game_signaled=False,
        executor_in_game_confirmed=True,
    )


def test_no_timeout_failure_when_signaled_despite_deadline() -> None:
    assert not should_return_parallel_timeout_failure(
        timed_out=True,
        phase_ok=True,
        in_game_signaled=True,
        executor_in_game_confirmed=None,
        executor_enabled=True,
    )


def test_attempt_context_in_game_signal() -> None:
    ctx = AttemptContext()
    assert not ctx.is_in_game_confirmed()
    ctx.signal_in_game_confirmed("tutorial visible")
    assert ctx.is_in_game_confirmed()
    assert ctx.get_in_game_note() == "tutorial visible"
