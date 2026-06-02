from __future__ import annotations

import logging
from pathlib import Path

from game_agent.utils.gameturbo_bootstrap import PACKAGES_DIR

logger = logging.getLogger(__name__)


def list_deploy_artifact_names(packages_dir: Path = PACKAGES_DIR) -> list[str]:
    if not packages_dir.is_dir():
        return []
    return sorted(
        path.name
        for path in packages_dir.iterdir()
        if path.is_file() and path.name.startswith("game_gameturbo")
    )


def cleanup_deploy_artifacts(packages_dir: Path = PACKAGES_DIR) -> list[str]:
    """删除 deploy 产物（game_gameturbo.apk、签名文件等），保留原包。"""
    removed: list[str] = []
    if not packages_dir.is_dir():
        return removed
    for path in packages_dir.iterdir():
        if path.is_file() and path.name.startswith("game_gameturbo"):
            path.unlink()
            removed.append(path.name)
    if removed:
        logger.info("已清理 packages 下 deploy 产物: %s", ", ".join(removed))
    return removed


def remove_source_apk(source_apk: Path | None) -> bool:
    """任务最终产出完成后删除原包（仅调用一次）。"""
    if source_apk is None or not source_apk.is_file():
        return False
    name = source_apk.name
    source_apk.unlink()
    logger.info("已删除原包: %s", name)
    return True
