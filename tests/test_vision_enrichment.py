from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from game_agent.graphs.launch_facts import needs_async_vision_enrichment
from game_agent.graphs.vision_enrichment import VisionEnrichmentQueue
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state


def test_needs_async_vision_false_on_login() -> None:
    facts = LaunchFacts(login_blocking=True, login_stage="login_form")
    assert needs_async_vision_enrichment(facts) is False


def test_needs_async_vision_false_on_privacy_modal() -> None:
    facts = LaunchFacts(initial_privacy_dialog=True)
    assert needs_async_vision_enrichment(facts) is False


def test_needs_async_vision_true_on_download() -> None:
    facts = LaunchFacts(download_visible=True)
    assert needs_async_vision_enrichment(facts) is True


def test_needs_async_vision_false_on_unknown_default() -> None:
    facts = LaunchFacts(classify_reason="no routing")
    assert needs_async_vision_enrichment(facts) is False


def test_needs_sync_false_when_server_has_enter_cta() -> None:
    from game_agent.graphs.launch_facts import needs_sync_interpretation

    facts = LaunchFacts(server_slot_visible=True, enter_cta_xy=(500, 800))
    assert needs_sync_interpretation(facts) is False


def test_vision_queue_merge_when_task_done() -> None:
    llm = MagicMock()
    queue = VisionEnrichmentQueue(llm_cfg=llm)
    state = empty_launch_graph_state()
    state["facts"] = LaunchFacts(classify_reason="x").model_dump()
    state["last_screenshot"] = "/tmp/shot.png"

    async def _fake_vision() -> str:
        return '{"stage":"login","has_anomaly":false,"anomaly_reason":"","progress":""}'

    async def _main() -> dict:
        queue._screenshot_path = "/tmp/shot.png"
        queue._task = asyncio.create_task(_fake_vision())
        await queue._task
        return queue.merge_if_ready(state)

    merged = asyncio.run(_main())

    assert merged.get("vision_enrichment_status") == "done"
    assert "vision_stage" in (merged.get("facts") or {})
