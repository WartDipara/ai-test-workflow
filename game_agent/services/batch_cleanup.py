"""批跑结束后归档 batch_manifest 并清理 batch_* 中间目录。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from game_agent.controllers.task_queue import ApkTask, BatchManifest
from game_agent.models.task_runtime import TaskRuntimeRegistry
from game_agent.services.run_deliverable import task_output_dir

logger = logging.getLogger(__name__)


def resolve_deliverable_for_task(
    run_outputs_dir: Path,
    task: ApkTask,
) -> Path | None:
    """定位任务最终产出目录 run_outputs/{gid}_{task_id}/。"""
    runtime = TaskRuntimeRegistry.get(task.task_id)
    gid = (runtime.gid if runtime else "").strip()
    if gid:
        candidate = task_output_dir(run_outputs_dir, gid, task.task_id)
        if candidate.is_dir():
            return candidate
    matches = [
        p for p in run_outputs_dir.glob(f"*_{task.task_id}")
        if p.is_dir() and not p.name.startswith("batch_")
    ]
    if len(matches) == 1:
        return matches[0].resolve()
    return None


def archive_batch_manifest(
    manifest: BatchManifest,
    run_outputs_dir: Path,
) -> list[str]:
    """将 batch_manifest.json 复制到各任务 deliverable 目录。"""
    if not manifest.path.is_file():
        return []
    archived: list[str] = []
    seen: set[str] = set()
    for task in manifest.tasks:
        deliverable = resolve_deliverable_for_task(run_outputs_dir, task)
        if deliverable is None:
            logger.debug(
                "跳过 manifest 归档：未找到 deliverable task_id=%s",
                task.task_id,
            )
            continue
        key = str(deliverable.resolve())
        if key in seen:
            continue
        seen.add(key)
        dst = deliverable / "batch_manifest.json"
        shutil.copy2(manifest.path, dst)
        archived.append(str(dst.resolve()))
    return archived


def cleanup_batch_workspace(
    batch_root: Path,
    manifest: BatchManifest,
    *,
    run_outputs_dir: Path,
) -> tuple[list[str], list[str]]:
    """
    归档 manifest 到 {gid}_{task_id}/，删除整个 batch_* 目录（含 task_*/apk_cache）。
    """
    archived = archive_batch_manifest(manifest, run_outputs_dir)
    removed: list[str] = []
    failed: list[str] = []
    if batch_root.is_dir():
        try:
            shutil.rmtree(batch_root)
            removed.append(str(batch_root.resolve()))
            logger.info("已清理批跑中间目录: %s", batch_root)
        except OSError as exc:
            msg = f"{batch_root}: {exc}"
            failed.append(msg)
            logger.warning("批跑中间目录清理失败: %s", msg)
    if archived:
        logger.info("batch_manifest 已归档: %s", archived)
    return archived, failed
