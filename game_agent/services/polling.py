from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CALLBACK_HINT = "[System callback] Tool finished; the runtime continues your next turn automatically."


@dataclass(frozen=True, slots=True)
class PollOutcome:
    """Result of a blocking poll loop (sync or async)."""

    ok: bool
    polls: int
    aborted: bool = False
    satisfied_before_poll: bool = False


def poll_until_sync(
    *,
    predicate: Callable[[], bool],
    timeout_s: float,
    interval_s: float,
    should_abort: Callable[[], bool] | None = None,
    log_prefix: str = "Poll",
) -> PollOutcome:
    """Blocking poll until ``predicate()`` is true, timeout, or abort."""
    timeout_s = max(0.1, float(timeout_s))
    interval_s = max(0.05, float(interval_s))
    if predicate():
        return PollOutcome(ok=True, polls=0, satisfied_before_poll=True)

    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        if should_abort and should_abort():
            logger.warning("[%s] aborted after %d poll(s)", log_prefix, attempt)
            return PollOutcome(ok=False, polls=attempt, aborted=True)
        attempt += 1
        if predicate():
            logger.info("[%s] ok after %d poll(s)", log_prefix, attempt)
            return PollOutcome(ok=True, polls=attempt)
        remaining = deadline - time.monotonic()
        logger.debug(
            "[%s] poll #%d not ready, ~%.0fs left",
            log_prefix,
            attempt,
            max(0.0, remaining),
        )
        time.sleep(min(interval_s, max(0.05, remaining)))

    logger.warning("[%s] timeout after %d poll(s)", log_prefix, attempt)
    return PollOutcome(ok=False, polls=attempt)


async def poll_until_async(
    *,
    predicate: Callable[[], bool | Awaitable[bool]],
    timeout_s: float,
    interval_s: float,
    should_abort: Callable[[], bool] | None = None,
    log_prefix: str = "Poll",
) -> PollOutcome:
    """Async poll with ``asyncio.sleep`` between attempts."""

    async def check() -> bool:
        value = predicate()
        if asyncio.iscoroutine(value):
            return await value
        return bool(value)

    timeout_s = max(0.1, float(timeout_s))
    interval_s = max(0.05, float(interval_s))
    if await check():
        return PollOutcome(ok=True, polls=0, satisfied_before_poll=True)

    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        if should_abort and should_abort():
            logger.warning("[%s] aborted after %d poll(s)", log_prefix, attempt)
            return PollOutcome(ok=False, polls=attempt, aborted=True)
        attempt += 1
        if await check():
            logger.info("[%s] ok after %d poll(s)", log_prefix, attempt)
            return PollOutcome(ok=True, polls=attempt)
        remaining = deadline - time.monotonic()
        logger.debug(
            "[%s] poll #%d not ready, ~%.0fs left",
            log_prefix,
            attempt,
            max(0.0, remaining),
        )
        await asyncio.sleep(min(interval_s, max(0.05, remaining)))

    logger.warning("[%s] timeout after %d poll(s)", log_prefix, attempt)
    return PollOutcome(ok=False, polls=attempt)
