from __future__ import annotations

from pathlib import Path
from typing import Any

from game_agent.models.settings import AppConfig, GameSection, GameTurboSection
from game_agent.models.task_runtime import TaskRuntime


def normalize_launch_activity(package_name: str, launch_activity: str) -> str:
    package_name = package_name.strip()
    launch_activity = launch_activity.strip()
    if not package_name or not launch_activity:
        return launch_activity
    if "/" not in launch_activity:
        return f"{package_name}/{launch_activity}"
    if launch_activity.startswith("/"):
        return f"{package_name}{launch_activity}"
    return launch_activity


class MergedGameSection:
    """game 段超时配置 + TaskRuntime 包名/Activity（仅内存，不入 settings.yaml）。"""

    __slots__ = ("_base", "_runtime")

    def __init__(self, base: GameSection, runtime: TaskRuntime) -> None:
        self._base = base
        self._runtime = runtime

    @property
    def package_name(self) -> str:
        return self._runtime.package_name

    @property
    def launch_activity(self) -> str:
        return self._runtime.normalized_launch_activity

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


class MergedGameturboSection:
    """gameturbo 静态配置 + TaskRuntime 中的 gid/路径。"""

    __slots__ = ("_base", "_runtime")

    def __init__(self, base: GameTurboSection, runtime: TaskRuntime) -> None:
        self._base = base
        self._runtime = runtime

    @property
    def gid(self) -> str:
        return self._runtime.gid

    @property
    def game_config_path(self) -> Path | None:
        return self._runtime.game_config_path

    @property
    def source_apk(self) -> Path | None:
        return self._runtime.source_apk

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


class TaskConfig:
    """AppConfig 与 TaskRuntime 合并视图，供整条流水线只读使用。"""

    __slots__ = ("_base", "_runtime", "game", "gameturbo")

    def __init__(self, base: AppConfig, runtime: TaskRuntime) -> None:
        self._base = base
        self._runtime = runtime
        self.game = MergedGameSection(base.game, runtime)
        self.gameturbo = MergedGameturboSection(base.gameturbo, runtime)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)

    @property
    def base(self) -> AppConfig:
        return self._base

    @property
    def runtime(self) -> TaskRuntime:
        return self._runtime
