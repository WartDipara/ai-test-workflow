from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from game_agent.paths import gameturbo_merged_config_path
from game_agent.utils.apk_util import get_apk_launch_info
from game_agent.utils.gameturbo_bootstrap import output_apk_path


@dataclass(slots=True)
class TaskRuntime:
    """单任务运行时状态（包名、gid、路径等），不写入 settings.yaml。"""

    task_id: str
    index: int
    serial: str
    apk_url: str
    batch_root: Path
    task_cache_dir: Path
    gid: str = ""
    package_name: str = ""
    launch_activity: str = ""
    source_apk: Path | None = None
    game_config_path: Path | None = None

    @property
    def output_apk(self) -> Path | None:
        if not self.gid:
            return None
        return output_apk_path(self.gid)

    @property
    def merged_config_path(self) -> Path | None:
        if not self.gid:
            return None
        return gameturbo_merged_config_path(self.gid)

    def update_from_apk(self, apk_path: Path) -> None:
        info = get_apk_launch_info(apk_path)
        if info is None:
            return
        self.package_name = info.package_name
        self.launch_activity = info.launch_activity

    def update_gameturbo(
        self,
        *,
        gid: str,
        source_apk: Path,
        game_config_path: Path,
    ) -> None:
        self.gid = gid.strip()
        self.source_apk = source_apk.resolve()
        self.game_config_path = game_config_path.resolve()

    @property
    def normalized_launch_activity(self) -> str:
        package_name = self.package_name.strip()
        launch_activity = self.launch_activity.strip()
        if not package_name or not launch_activity:
            return launch_activity
        if "/" not in launch_activity:
            return f"{package_name}/{launch_activity}"
        if launch_activity.startswith("/"):
            return f"{package_name}{launch_activity}"
        return launch_activity

    def require_identity(self) -> None:
        if not self.package_name.strip():
            raise RuntimeError("TaskRuntime 缺少 package_name（需先完成 APK 预处理）")
        if not self.launch_activity.strip():
            raise RuntimeError("TaskRuntime 缺少 launch_activity（需先完成 APK 预处理）")

    def require_gameturbo(self) -> None:
        if not self.gid.strip():
            raise RuntimeError("TaskRuntime 缺少 gid")
        if self.game_config_path is None or not self.game_config_path.is_file():
            raise RuntimeError("TaskRuntime 缺少有效的 game_config_path")


class TaskRuntimeRegistry:
    """进程内 task_id → TaskRuntime 索引。"""

    _by_task_id: dict[str, TaskRuntime] = {}
    _guard = threading.Lock()

    @classmethod
    def register(cls, runtime: TaskRuntime) -> None:
        with cls._guard:
            cls._by_task_id[runtime.task_id] = runtime

    @classmethod
    def get(cls, task_id: str) -> TaskRuntime | None:
        return cls._by_task_id.get(task_id)

    @classmethod
    def get_by_gid(cls, gid: str) -> TaskRuntime | None:
        gid = (gid or "").strip()
        if not gid:
            return None
        with cls._guard:
            for runtime in cls._by_task_id.values():
                if runtime.gid == gid:
                    return runtime
        return None

    @classmethod
    def clear(cls) -> None:
        with cls._guard:
            cls._by_task_id.clear()
