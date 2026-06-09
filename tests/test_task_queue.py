from __future__ import annotations

import threading
from pathlib import Path

import pytest

from game_agent.controllers.task_queue import (
    ApkTaskStatus,
    GlobalTaskQueue,
    TaskQueueLock,
    build_tasks_from_urls,
)


@pytest.fixture(autouse=True)
def _reset_queue() -> None:
    GlobalTaskQueue.reset()
    yield
    GlobalTaskQueue.reset()


def test_build_tasks_from_urls() -> None:
    tasks = build_tasks_from_urls(["http://a/a.apk", "http://b/b.apk"])
    assert len(tasks) == 2
    assert tasks[0].index == 0
    assert tasks[1].index == 1
    assert tasks[0].status == ApkTaskStatus.PENDING


def test_claim_next_is_atomic(tmp_path: Path) -> None:
    queue = GlobalTaskQueue.init(
        ["http://a/a.apk", "http://b/b.apk", "http://c/c.apk"],
        tmp_path,
    )
    claimed: list[str] = []
    lock = threading.Lock()

    def worker(serial: str) -> None:
        while True:
            task = queue.claim_next(serial)
            if task is None:
                return
            with lock:
                claimed.append(f"{serial}:{task.index}")

    threads = [
        threading.Thread(target=worker, args=(f"dev{i}",), daemon=True)
        for i in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed) == 3
    indexes = sorted(int(item.split(":")[1]) for item in claimed)
    assert indexes == [0, 1, 2]


def test_task_queue_lock_exclusive(tmp_path: Path) -> None:
    lock_path = tmp_path / ".task_queue.lock"
    with TaskQueueLock(lock_path):
        with pytest.raises(RuntimeError, match="另一进程"):
            TaskQueueLock(lock_path).acquire()
