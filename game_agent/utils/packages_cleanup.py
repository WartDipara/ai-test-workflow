from __future__ import annotations

import logging
from pathlib import Path

from game_agent.utils.gameturbo_bootstrap import PACKAGES_DIR, output_apk_name

logger = logging.getLogger(__name__)


def deploy_artifact_prefix(gid: str | None = None) -> str:
    gid = (gid or "").strip()
    if gid:
        return output_apk_name(gid).removesuffix(".apk")
    return "game_gameturbo"


def list_deploy_artifact_names(
    packages_dir: Path = PACKAGES_DIR,
    *,
    gid: str | None = None,
) -> list[str]:
    if not packages_dir.is_dir():
        return []
    prefix = deploy_artifact_prefix(gid)
    return sorted(
        path.name
        for path in packages_dir.iterdir()
        if path.is_file() and path.name.startswith(prefix)
    )


def cleanup_deploy_artifacts(
    packages_dir: Path = PACKAGES_DIR,
    *,
    gid: str | None = None,
) -> list[str]:
    """删除指定 gid 的 deploy 产物，保留原包。"""
    removed: list[str] = []
    if not packages_dir.is_dir():
        return removed
    prefix = deploy_artifact_prefix(gid)
    for path in packages_dir.iterdir():
        if path.is_file() and path.name.startswith(prefix):
            path.unlink()
            removed.append(path.name)
    if removed:
        logger.info("已清理 packages 下 deploy 产物: %s", ", ".join(removed))
    return removed


def cleanup_task_packages(
    gid: str,
    source_apk: Path | None,
    packages_dir: Path = PACKAGES_DIR,
) -> dict[str, list[str]]:
    """按 gid 精准删除原包与 deploy 产物。"""
    deploy_removed = cleanup_deploy_artifacts(packages_dir, gid=gid)
    source_removed: list[str] = []
    if source_apk is not None and source_apk.is_file():
        name = source_apk.name
        source_apk.resolve().unlink()
        source_removed.append(name)
        logger.info("已删除原包: %s", name)
    return {
        "deploy": deploy_removed,
        "source": source_removed,
    }
