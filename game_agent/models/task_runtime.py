from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from game_agent.utils.apk_util import get_apk_launch_info


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
    install_apk: Path | None = None
    game_config_path: Path | None = None
    plugin_merged_config: Path | None = field(default=None, repr=False)

    @property
    def output_apk(self) -> Path | None:
        if self.install_apk is not None and self.install_apk.is_file():
            return self.install_apk.resolve()
        return None

    @property
    def merged_config_path(self) -> Path | None:
        if self.plugin_merged_config is not None and self.plugin_merged_config.is_file():
            return self.plugin_merged_config.resolve()
        return None

    def update_from_apk(self, apk_path: Path) -> None:
        info = get_apk_launch_info(apk_path)
        if info is None:
            return
        self.package_name = info.package_name
        self.launch_activity = info.launch_activity

    def update_install_apk(self, apk_path: Path) -> None:
        self.install_apk = apk_path.resolve()

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

    def update_plugin_merged_config(self, path: Path | None) -> None:
        self.plugin_merged_config = path.resolve() if path is not None else None

    def require_install_apk(self) -> Path:
        if self.install_apk is None or not self.install_apk.is_file():
            raise RuntimeError("TaskRuntime missing valid install_apk")
        return self.install_apk.resolve()

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
            raise RuntimeError("TaskRuntime missing package_name (run APK preprocess first)")
        if not self.launch_activity.strip():
            raise RuntimeError("TaskRuntime missing launch_activity (run APK preprocess first)")

    def require_gameturbo(self) -> None:
        if not self.gid.strip():
            raise RuntimeError("TaskRuntime missing gid")
        if self.game_config_path is None or not self.game_config_path.is_file():
            raise RuntimeError("TaskRuntime missing valid game_config_path")


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
