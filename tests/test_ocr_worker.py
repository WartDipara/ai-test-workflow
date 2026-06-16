"""OcrWorker 串行化与 per-key 隔离。"""

from __future__ import annotations

import threading
import time

from game_agent.models.settings import OcrSection
from game_agent.utils.ocr_worker import configure_ocr_worker, get_ocr_worker, shutdown_ocr_worker


def test_ocr_worker_serializes_calls() -> None:
    key = "test-serial-worker"
    shutdown_ocr_worker(key)
    worker = configure_ocr_worker(OcrSection(), worker_key=key)
    order: list[int] = []
    lock = threading.Lock()

    def job(n: int) -> int:
        with lock:
            order.append(n)
        time.sleep(0.02)
        return n * 2

    t1 = threading.Thread(target=lambda: worker.submit(job, 1))
    t2 = threading.Thread(target=lambda: worker.submit(job, 2))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert order == [1, 2]
    shutdown_ocr_worker(key)


def test_ocr_workers_isolated_by_key() -> None:
    k1, k2 = "device-a", "device-b"
    shutdown_ocr_worker(k1)
    shutdown_ocr_worker(k2)
    w1 = configure_ocr_worker(OcrSection(), worker_key=k1)
    w2 = configure_ocr_worker(OcrSection(device_policy="cpu"), worker_key=k2)
    assert w1 is not w2
    assert get_ocr_worker(worker_key=k1) is w1
    assert get_ocr_worker(worker_key=k2) is w2
    shutdown_ocr_worker(k1)
    shutdown_ocr_worker(k2)
