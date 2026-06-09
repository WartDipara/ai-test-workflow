from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from game_agent.controllers import batch_runner
from game_agent.controllers.task_queue import ApkTaskStatus, GlobalTaskQueue
from game_agent.services.shutdown import get_shutdown_context, reset_shutdown_context


def test_batch_worker_stops_claiming_on_shutdown(tmp_path: Path) -> None:
    reset_shutdown_context()
    batch_root = tmp_path / "batch"
    batch_root.mkdir()
    queue = GlobalTaskQueue.init(["http://a", "http://b", "http://c"], batch_root)

    claimed: list[int] = []
    lock = threading.Lock()
    orchestrator_started = threading.Event()
    release_orchestrator = threading.Event()

    class SlowOrchestrator:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run(self) -> int:
            task = queue.all_tasks()[len(claimed) - 1]
            with lock:
                claimed.append(task.index)
            orchestrator_started.set()
            release_orchestrator.wait(timeout=5.0)
            return 0

    ctx = get_shutdown_context()

    def worker() -> None:
        while not ctx.is_requested():
            task = queue.claim_next("dev1")
            if task is None:
                return
            orch = SlowOrchestrator()
            orch.run()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert orchestrator_started.wait(timeout=2.0)
    ctx.request_shutdown("test")
    release_orchestrator.set()
    thread.join(timeout=3.0)

    assert len(claimed) == 1
    remaining = [t for t in queue.all_tasks() if t.status == ApkTaskStatus.PENDING]
    assert len(remaining) == 2
    reset_shutdown_context()
