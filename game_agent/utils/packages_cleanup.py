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


def remove_source_apk(source_apk: Path | None, packages_dir: Path = PACKAGES_DIR) -> list[str]:
    """删除原包 APK（任务最终结束时调用）。"""
    removed: list[str] = []
    candidates: list[Path] = []
    if source_apk is not None and source_apk.is_file():
        candidates.append(source_apk.resolve())
    if packages_dir.is_dir():
        for apk in packages_dir.glob("*.apk"):
            if apk.name.startswith("game_gameturbo"):
                continue
            if "gameturbo" in apk.name.lower():
                continue
            resolved = apk.resolve()
            if resolved not in candidates:
                candidates.append(resolved)
    for path in candidates:
        name = path.name
        path.unlink()
        removed.append(name)
        logger.info("已删除原包: %s", name)
    return removed


def clear_packages_directory(packages_dir: Path = PACKAGES_DIR) -> list[str]:
    """任务结束后清空 packages 目录下所有文件（原包 + deploy 产物）。"""
    removed: list[str] = []
    if not packages_dir.is_dir():
        packages_dir.mkdir(parents=True, exist_ok=True)
        return removed
    for path in list(packages_dir.iterdir()):
        if path.is_file():
            path.unlink()
            removed.append(path.name)
    if removed:
        logger.info("已清空 packages 目录: %s", ", ".join(removed))
    elif packages_dir.is_dir():
        logger.info("packages 目录已为空: %s", packages_dir)
    return removed


def prepare_packages_for_new_task(packages_dir: Path = PACKAGES_DIR) -> list[str]:
    """
    新任务（run.sh / main）开始前清空 packages/ 下全部文件。

    与任务结束时的 finalize_task_packages 对称，用于消化异常中断（断电等）
    未执行收尾时遗留的原包、deploy 产物与签名文件。预处理会重新放入原包。
    """
    removed = clear_packages_directory(packages_dir)
    if removed:
        logger.info(
            "新任务开始前已清空 packages 遗留（%d 个）: %s",
            len(removed),
            ", ".join(removed[:12]) + (" …" if len(removed) > 12 else ""),
        )
    return removed


def finalize_task_packages(
    packages_dir: Path = PACKAGES_DIR,
    source_apk: Path | None = None,
) -> dict[str, list[str]]:
    """
    任务最终收尾：先删 deploy 产物，再删原包，最后扫尾清空残留文件。
    """
    deploy_removed = cleanup_deploy_artifacts(packages_dir)
    source_removed = remove_source_apk(source_apk, packages_dir)
    leftover_removed = clear_packages_directory(packages_dir)
    return {
        "deploy": deploy_removed,
        "source": source_removed,
        "leftover": leftover_removed,
    }
