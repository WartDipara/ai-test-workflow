from __future__ import annotations

import logging
from pathlib import Path

from game_agent.external_services.base import PreparedApp
from game_agent.models.task_runtime import TaskRuntime
from game_agent.modules.preprocessing.apk_resolver import resolve_apk_for_preprocess
from game_agent.modules.preprocessing.preprocessor import PreprocessResult
from game_agent.utils.apk_util import get_apk_launch_info

logger = logging.getLogger(__name__)


def sync_runtime_from_apk(runtime: TaskRuntime, apk_path: Path) -> None:
    runtime.source_apk = apk_path.resolve()
    runtime.install_apk = apk_path.resolve()
    runtime.update_from_apk(apk_path)


def resolve_core_apk(
    *,
    cache_dir: Path,
    runtime: TaskRuntime,
    preprocess_record: PreprocessResult | None,
) -> Path | None:
    if runtime.install_apk is not None and runtime.install_apk.is_file():
        return runtime.install_apk.resolve()
    if preprocess_record is not None and preprocess_record.processed_apk is not None:
        apk = preprocess_record.processed_apk
        if apk.is_file():
            return apk.resolve()
    if runtime.source_apk is not None and runtime.source_apk.is_file():
        return runtime.source_apk.resolve()
    resolved = resolve_apk_for_preprocess(cache_dir)
    if resolved is None:
        return None
    return resolved.path.resolve()


def build_core_prepared_app(apk_path: Path, *, skip_install: bool = False) -> PreparedApp:
    info = get_apk_launch_info(apk_path)
    package_name = info.package_name if info else ""
    launch_activity = info.launch_activity if info else ""
    return PreparedApp(
        install_apk=apk_path.resolve(),
        source_apk=apk_path.resolve(),
        package_name=package_name,
        launch_activity=launch_activity,
        skip_install=skip_install,
        prepared_by="core",
    )
