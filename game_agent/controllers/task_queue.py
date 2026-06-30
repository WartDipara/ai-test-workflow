from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from game_agent.services.run_deliverable import new_task_id

logger = logging.getLogger(__name__)


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        raw = lock_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    return int(raw)


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _clear_stale_lock(lock_path: Path) -> bool:
    pid = _read_lock_pid(lock_path)
    if pid is None or _process_alive(pid):
        return False
    try:
        lock_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Failed to clean stale task queue lock %s: %s", lock_path, exc)
        return False
    logger.warning("Cleaned stale task queue lock %s (holder pid=%s exited)", lock_path, pid)
    return True


class ApkTaskStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class ApkTask:
    task_id: str
    index: int
    url: str
    status: ApkTaskStatus = ApkTaskStatus.PENDING
    claimed_by_serial: str = ""
    started_at: str = ""
    finished_at: str = ""
    result_code: int | None = None
    output_dir: str = ""
    error: str = ""


@dataclass
class BatchManifest:
    batch_root: Path
    devices: list[str]
    tasks: list[ApkTask]
    created_at: str = field(default_factory=lambda: _utc_now())
    finished_at: str = ""

    @property
    def path(self) -> Path:
        return self.batch_root / "batch_manifest.json"

    def save(self) -> None:
        self.batch_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "batch_root": str(self.batch_root),
            "devices": self.devices,
            "tasks": [asdict(task) for task in self.tasks],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def update_from_queue(self, queue: GlobalTaskQueue) -> None:
        self.tasks = list(queue.all_tasks())

    def finalize(self, queue: GlobalTaskQueue) -> None:
        self.update_from_queue(queue)
        self.finished_at = _utc_now()


class TaskQueueLock:
    """进程级文件锁，防止两个 runner 同时消费同一批任务。"""

    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path.resolve()
        self._fd: int | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                self._fd = fd
                logger.info("Acquired task queue lock: %s", self._path)
                return
            except FileExistsError as exc:
                if attempt == 0 and _clear_stale_lock(self._path):
                    continue
                holder = ""
                try:
                    holder = self._path.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    pass
                detail = f" (holder pid={holder})" if holder else ""
                raise RuntimeError(
                    f"另一进程正在消费任务队列: {self._path}{detail}",
                ) from exc

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to release task queue lock: %s", exc)
        else:
            logger.info("Released task queue lock: %s", self._path)

    def __enter__(self) -> TaskQueueLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


class GlobalTaskQueue:
    """进程内单例任务队列，claim_next 原子认领。"""

    _instance: GlobalTaskQueue | None = None
    _instance_guard = threading.Lock()

    def __init__(self, tasks: list[ApkTask], batch_root: Path) -> None:
        self._tasks = tasks
        self._batch_root = batch_root.resolve()
        self._lock = threading.Lock()
        self._by_id = {task.task_id: task for task in tasks}

    @classmethod
    def init(cls, urls: list[str], batch_root: Path) -> GlobalTaskQueue:
        with cls._instance_guard:
            if cls._instance is not None:
                raise RuntimeError("GlobalTaskQueue already initialized")
            tasks = [
                ApkTask(task_id=new_task_id(), index=index, url=url)
                for index, url in enumerate(urls)
            ]
            cls._instance = cls(tasks, batch_root)
            return cls._instance

    @classmethod
    def get(cls) -> GlobalTaskQueue | None:
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_guard:
            cls._instance = None

    def all_tasks(self) -> list[ApkTask]:
        return list(self._tasks)

    def claim_next(self, serial: str) -> ApkTask | None:
        serial = (serial or "").strip()
        if not serial:
            return None
        with self._lock:
            for task in self._tasks:
                if task.status != ApkTaskStatus.PENDING:
                    continue
                task.status = ApkTaskStatus.CLAIMED
                task.claimed_by_serial = serial
                task.started_at = _utc_now()
                task.status = ApkTaskStatus.RUNNING
                task.output_dir = str(
                    (self._batch_root / f"task_{task.index}").resolve(),
                )
                logger.info(
                    "Device %s claimed task index=%d task_id=%s",
                    serial,
                    task.index,
                    task.task_id,
                )
                return task
        return None

    def mark_finished(
        self,
        task_id: str,
        *,
        success: bool,
        result_code: int,
        error: str = "",
    ) -> None:
        with self._lock:
            task = self._by_id.get(task_id)
            if task is None:
                raise KeyError(f"Unknown task_id: {task_id}")
            task.status = ApkTaskStatus.SUCCEEDED if success else ApkTaskStatus.FAILED
            task.finished_at = _utc_now()
            task.result_code = result_code
            task.error = (error or "")[:2000]


def build_tasks_from_urls(urls: list[str]) -> list[ApkTask]:
    return [
        ApkTask(task_id=new_task_id(), index=index, url=url)
        for index, url in enumerate(urls)
    ]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
