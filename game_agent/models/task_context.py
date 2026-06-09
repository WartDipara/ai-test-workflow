from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from game_agent.controllers.task_queue import ApkTask
from game_agent.models.task_runtime import TaskRuntime


@dataclass(slots=True, frozen=True)
class TaskContext:
    """批跑任务上下文：封装 TaskRuntime 并提供便捷访问。"""

    runtime: TaskRuntime

    @property
    def task_id(self) -> str:
        return self.runtime.task_id

    @property
    def index(self) -> int:
        return self.runtime.index

    @property
    def serial(self) -> str:
        return self.runtime.serial

    @property
    def apk_url(self) -> str:
        return self.runtime.apk_url

    @property
    def batch_root(self) -> Path:
        return self.runtime.batch_root

    @property
    def task_cache_dir(self) -> Path:
        return self.runtime.task_cache_dir

    @property
    def gid(self) -> str:
        return self.runtime.gid

    @property
    def source_apk(self) -> Path | None:
        return self.runtime.source_apk

    @classmethod
    def from_claimed_task(
        cls,
        task: ApkTask,
        *,
        serial: str,
        batch_root: Path,
        global_cache_dir: Path,
    ) -> TaskContext:
        if task.url:
            task_cache_dir = (batch_root / f"task_{task.index}" / "apk_cache").resolve()
            apk_url = task.url
        else:
            task_cache_dir = global_cache_dir.resolve()
            apk_url = ""
        runtime = TaskRuntime(
            task_id=task.task_id,
            index=task.index,
            serial=serial,
            apk_url=apk_url,
            batch_root=batch_root.resolve(),
            task_cache_dir=task_cache_dir,
        )
        return cls(runtime=runtime)
