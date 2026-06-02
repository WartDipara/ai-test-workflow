from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from game_agent.models.worker_task import (
    WorkerProgress,
    WorkerTaskResult,
    WorkerTaskSnapshot,
    WorkerTaskStatus,
    utc_now,
)

logger = logging.getLogger(__name__)

WorkerCoroutine = Callable[[str, Callable[[WorkerProgress], None]], Awaitable[WorkerTaskResult]]


class WorkerTaskRegistry:
    """记录职员任务状态、心跳、结果与超时，避免主脑依赖阻塞式等待。"""

    def __init__(self, *, timeout_s: float, heartbeat_timeout_s: float) -> None:
        self._timeout_s = max(1.0, float(timeout_s))
        self._heartbeat_timeout_s = max(1.0, float(heartbeat_timeout_s))
        self._tasks: dict[str, WorkerTaskSnapshot] = {}
        self._handles: dict[str, asyncio.Task[None]] = {}

    def submit(
        self,
        *,
        worker_name: str,
        round_id: int,
        screenshot_path: Path,
        worker: WorkerCoroutine,
    ) -> str:
        task_id = f"{worker_name}_round_{round_id:03d}"
        now = utc_now()
        snapshot = WorkerTaskSnapshot(
            task_id=task_id,
            worker_name=worker_name,
            round_id=round_id,
            screenshot_path=screenshot_path,
            status="pending",
            progress=0,
            current_step="queued",
            message="任务已创建，等待职员开始执行",
            created_at=now,
            updated_at=now,
        )
        self._tasks[task_id] = snapshot
        self._handles[task_id] = asyncio.create_task(self._run(task_id, worker))
        return task_id

    def snapshot(self, task_id: str) -> WorkerTaskSnapshot:
        snapshot = self._tasks[task_id]
        handle = self._handles.get(task_id)
        if snapshot.is_done:
            return snapshot
        now = utc_now()
        if (now - snapshot.created_at).total_seconds() > self._timeout_s:
            self._set_terminal(
                task_id,
                status="timeout",
                message=f"职员任务超过总超时 {self._timeout_s:.1f}s",
            )
            if handle is not None:
                handle.cancel()
        elif (now - snapshot.updated_at).total_seconds() > self._heartbeat_timeout_s:
            self._tasks[task_id] = replace(
                snapshot,
                status="reporting",
                message=(
                    f"职员已有 {self._heartbeat_timeout_s:.1f}s 未更新心跳；"
                    "模型请求可能仍在进行，等待总超时判定"
                ),
                updated_at=now,
            )
        return self._tasks[task_id]

    def is_done(self, task_id: str) -> bool:
        return self.snapshot(task_id).is_done

    def cancel(self, task_id: str, message: str = "任务被调度器取消") -> None:
        handle = self._handles.get(task_id)
        if handle is not None:
            handle.cancel()
        self._set_terminal(task_id, status="cancelled", message=message)

    async def _run(self, task_id: str, worker: WorkerCoroutine) -> None:
        self._update(
            task_id,
            WorkerProgress(
                status="running",
                progress=5,
                current_step="worker_started",
                message="职员已开始处理任务",
            ),
        )
        try:
            result = await worker(task_id, lambda progress: self._update(task_id, progress))
        except asyncio.CancelledError:
            logger.info("worker task cancelled: %s", task_id)
            if not self._tasks[task_id].is_done:
                self._set_terminal(task_id, status="cancelled", message="职员任务被取消")
            return
        except Exception as e:
            logger.exception("worker task failed: %s", task_id)
            self._set_terminal(task_id, status="failed", message=str(e), error=str(e))
            return
        snapshot = self._tasks[task_id]
        self._tasks[task_id] = replace(
            snapshot,
            status="completed",
            progress=100,
            current_step="completed",
            message="职员任务已完成并提交最终报告",
            updated_at=utc_now(),
            completed_at=utc_now(),
            result=result,
        )

    def _update(self, task_id: str, progress: WorkerProgress) -> None:
        snapshot = self._tasks[task_id]
        if snapshot.is_done:
            return
        self._tasks[task_id] = replace(
            snapshot,
            status=progress.status,
            progress=max(0, min(100, int(progress.progress))),
            current_step=progress.current_step,
            message=progress.message,
            updated_at=progress.updated_at,
        )

    def _set_terminal(
        self,
        task_id: str,
        *,
        status: WorkerTaskStatus,
        message: str,
        error: str | None = None,
    ) -> None:
        snapshot = self._tasks[task_id]
        self._tasks[task_id] = replace(
            snapshot,
            status=status,
            message=message,
            error=error,
            updated_at=utc_now(),
            completed_at=utc_now(),
        )
