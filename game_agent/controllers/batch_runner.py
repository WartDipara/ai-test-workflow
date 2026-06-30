from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from game_agent.config.loader import load_app_config
from game_agent.config.paths import resolve_repo_path
from game_agent.core.deliverables import resolve_deliverables_dir
from game_agent.controllers.orchestrator import GameTestOrchestrator
from game_agent.controllers.task_queue import (
    ApkTaskStatus,
    BatchManifest,
    GlobalTaskQueue,
    TaskQueueLock,
)
from game_agent.models.task_context import TaskContext
from game_agent.models.task_runtime import TaskRuntimeRegistry
from game_agent.services.adb_devices import list_connected_devices
from game_agent.services.batch_cleanup import cleanup_batch_workspace
from game_agent.services.shutdown import ShutdownRequested, is_shutdown_requested

logger = logging.getLogger(__name__)

_JOIN_POLL_S = 0.5


def run_batch_orchestrator(config_path: Path, urls: list[str]) -> int:
    if not urls:
        logger.error("Batch run failed: no available APK sources (please configure apks.txt or apk_cache/*.apk)")
        return 1

    cfg = load_app_config(config_path)
    out_dir = resolve_repo_path(resolve_deliverables_dir(cfg))
    global_cache_dir = resolve_repo_path(cfg.preprocessing.apk_cache_dir)
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_root = (out_dir / f"batch_{batch_id}").resolve()
    batch_root.mkdir(parents=True, exist_ok=True)

    lock_path = out_dir / ".task_queue.lock"
    devices = list_connected_devices()
    if not devices:
        logger.error("Batch run failed: no devices in adb devices with state=device")
        cleanup_batch_workspace(
            batch_root,
            BatchManifest(batch_root=batch_root, devices=[], tasks=[]),
            run_outputs_dir=out_dir,
        )
        return 1

    interrupted = False
    manifest: BatchManifest | None = None

    try:
        with TaskQueueLock(lock_path):
            queue = GlobalTaskQueue.init(urls, batch_root)
            manifest = BatchManifest(
                batch_root=batch_root,
                devices=devices,
                tasks=queue.all_tasks(),
            )
            manifest.save()

            def worker(serial: str) -> None:
                while not is_shutdown_requested():
                    task = queue.claim_next(serial)
                    if task is None:
                        return
                    ctx = TaskContext.from_claimed_task(
                        task,
                        serial=serial,
                        batch_root=batch_root,
                        global_cache_dir=global_cache_dir,
                    )
                    TaskRuntimeRegistry.register(ctx.runtime)
                    try:
                        exit_code = GameTestOrchestrator(
                            config_path,
                            task_context=ctx,
                        ).run()
                        queue.mark_finished(
                            task.task_id,
                            success=exit_code == 0,
                            result_code=exit_code,
                        )
                    except ShutdownRequested as exc:
                        logger.warning(
                            "Batch run task interrupted by user index=%d serial=%s",
                            task.index,
                            serial,
                        )
                        from game_agent.models.run_failure import USER_INTERRUPT_MESSAGE

                        queue.mark_finished(
                            task.task_id,
                            success=False,
                            result_code=130,
                            error=USER_INTERRUPT_MESSAGE,
                        )
                        return
                    except Exception as exc:
                        logger.exception(
                            "Batch run task exception index=%d serial=%s",
                            task.index,
                            serial,
                        )
                        queue.mark_finished(
                            task.task_id,
                            success=False,
                            result_code=1,
                            error=str(exc),
                        )
                    manifest.update_from_queue(queue)
                    manifest.save()

            threads = [
                threading.Thread(
                    target=worker,
                    args=(serial,),
                    name=f"apk-worker-{serial}",
                    daemon=True,
                )
                for serial in devices
            ]
            for thread in threads:
                thread.start()

            while True:
                alive = [t for t in threads if t.is_alive()]
                if not alive:
                    break
                if is_shutdown_requested():
                    interrupted = True
                    logger.warning("Batch run received stop request, waiting for worker to finish…")
                for thread in alive:
                    thread.join(timeout=_JOIN_POLL_S)

            manifest.finalize(queue)
            manifest.save()
            GlobalTaskQueue.reset()

        if manifest is not None:
            archived, cleanup_failed = cleanup_batch_workspace(
                batch_root,
                manifest,
                run_outputs_dir=out_dir,
            )
            if cleanup_failed:
                logger.warning("Batch run cleanup failed: %s", cleanup_failed)
            elif archived:
                logger.debug("Batch run manifest archived: %d tasks", len(archived))

        if interrupted:
            failed = [t for t in manifest.tasks if t.status != ApkTaskStatus.SUCCEEDED]
            logger.error(
                "Batch run interrupted: %d/%d tasks incomplete, manifest=%s",
                len(failed),
                len(manifest.tasks),
                manifest.path,
            )
            return 130

        failed = [t for t in manifest.tasks if t.status != ApkTaskStatus.SUCCEEDED]
        if failed:
            logger.error(
                "Batch run finished: %d/%d tasks failed, manifest=%s",
                len(failed),
                len(manifest.tasks),
                manifest.path,
            )
            return 1

        logger.info("Batch run finished: all tasks succeeded, manifest=%s", manifest.path)
        return 0
    finally:
        TaskRuntimeRegistry.clear()
