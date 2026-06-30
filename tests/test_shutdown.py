from __future__ import annotations

import pytest

from game_agent.services.shutdown import (
    ShutdownContext,
    ShutdownRequested,
    get_shutdown_context,
    reset_shutdown_context,
)


@pytest.fixture(autouse=True)
def _reset_shutdown() -> None:
    reset_shutdown_context()
    yield
    reset_shutdown_context()


def test_shutdown_context_first_and_second_request() -> None:
    ctx = ShutdownContext()
    assert not ctx.is_requested()
    ctx.request_shutdown("SIGINT")
    assert ctx.is_requested()
    assert ctx.reason() == "SIGINT"
    assert not ctx.is_force()
    ctx.request_shutdown("SIGINT (force)", force=True)
    assert ctx.is_force()


def test_raise_if_requested() -> None:
    ctx = ShutdownContext()
    ctx.request_shutdown("test")
    with pytest.raises(ShutdownRequested) as exc_info:
        ctx.raise_if_requested()
    assert exc_info.value.reason == "test"


def test_global_shutdown_context_singleton() -> None:
    a = get_shutdown_context()
    b = get_shutdown_context()
    assert a is b


def test_classify_shutdown_exception() -> None:
    from game_agent.models.run_failure import classify_exception

    failure = classify_exception(ShutdownRequested("SIGINT"))
    assert failure.retryable is False
    assert failure.message == "User interrupted"
