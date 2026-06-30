"""进程闪退世代号：作废进行中的 VLM/API 结果。"""

from __future__ import annotations

import asyncio

from game_agent.graphs.vision_enrichment import VisionEnrichmentQueue
from game_agent.models.launch_graph_state import empty_launch_graph_state
from game_agent.modules.run_context import AttemptContext
from game_agent.modules.session_invalidation import (
    capture_session_generation,
    discard_if_stale,
)


def test_bump_marks_prior_generation_stale() -> None:
    ctx = AttemptContext()
    captured = capture_session_generation(ctx)
    assert captured == 0
    assert not ctx.is_session_generation_stale(captured)

    gen1 = ctx.bump_session_generation("process_gone")
    assert gen1 == 1
    assert ctx.is_session_generation_stale(0)
    assert not ctx.is_session_generation_stale(1)

    work = capture_session_generation(ctx)
    ctx.bump_session_generation("restart_confirmed")
    assert ctx.is_session_generation_stale(work)
    assert discard_if_stale(work, where="test", ctx=ctx)


def test_invalidate_event_set_on_bump() -> None:
    ctx = AttemptContext()
    assert not ctx.is_session_invalidated()
    ctx.bump_session_generation("gone")
    assert ctx.is_session_invalidated()
    ctx.acknowledge_session_invalidation()
    assert not ctx.is_session_invalidated()


def test_guard_session_work_invalidated_event() -> None:
    from game_agent.modules.session_invalidation import guard_session_work

    ctx = AttemptContext()
    state = {"session_work_generation": 0}
    assert not guard_session_work(state, ctx=ctx, where="test")
    ctx.bump_session_generation("gone")
    assert guard_session_work(state, ctx=ctx, where="test")


def test_guard_session_work_stale_generation() -> None:
    from game_agent.modules.session_invalidation import guard_session_work

    ctx = AttemptContext()
    ctx.bump_session_generation("gone")
    work = ctx.get_session_generation()
    ctx.bump_session_generation("restart")
    state = {"session_work_generation": work}
    assert guard_session_work(state, ctx=ctx, where="test")


def test_vision_enrichment_merge_discards_stale_generation() -> None:
    async def _run() -> None:
        ctx = AttemptContext()
        submit_gen = ctx.bump_session_generation("gone")
        queue = VisionEnrichmentQueue(llm_cfg=None, round_id=0)
        queue._submit_generation = submit_gen
        queue._screenshot_path = "/tmp/same.png"

        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        fut.set_result('{"stage":"dialogue"}')
        queue._task = fut  # type: ignore[assignment]

        state = empty_launch_graph_state()
        state["last_screenshot"] = "/tmp/same.png"
        ctx.bump_session_generation("restart")

        merged = queue.merge_if_ready(state, attempt_context=ctx)
        assert merged.get("vision_enrichment_status") != "merged"
        assert queue._task is None

    asyncio.run(_run())
