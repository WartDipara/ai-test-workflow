from __future__ import annotations

import json
from pathlib import Path

from game_agent.controllers.task_queue import ApkTask, ApkTaskStatus, BatchManifest
from game_agent.models.task_runtime import TaskRuntime, TaskRuntimeRegistry
from game_agent.services.batch_cleanup import (
    archive_batch_manifest,
    cleanup_batch_workspace,
    resolve_deliverable_for_task,
)


def test_resolve_deliverable_from_registry(tmp_path: Path) -> None:
    TaskRuntimeRegistry.clear()
    run_out = tmp_path / "run_outputs"
    deliverable = run_out / "16914_20260610_120000"
    deliverable.mkdir(parents=True)
    batch_root = run_out / "batch_20260610_120000"
    runtime = TaskRuntime(
        task_id="20260610_120000",
        index=0,
        serial="dev1",
        apk_url="http://example/apk",
        batch_root=batch_root,
        task_cache_dir=batch_root / "task_0" / "apk_cache",
        gid="16914",
    )
    TaskRuntimeRegistry.register(runtime)
    task = ApkTask(task_id="20260610_120000", index=0, url="http://example/apk")
    assert resolve_deliverable_for_task(run_out, task) == deliverable.resolve()
    TaskRuntimeRegistry.clear()


def test_cleanup_archives_manifest_and_removes_batch_root(tmp_path: Path) -> None:
    TaskRuntimeRegistry.clear()
    run_out = tmp_path / "run_outputs"
    batch_root = run_out / "batch_20260610_120000"
    task_dir = batch_root / "task_0" / "apk_cache"
    task_dir.mkdir(parents=True)
    (task_dir / "game.apk").write_bytes(b"apk")
    deliverable = run_out / "16914_20260610_120000"
    deliverable.mkdir(parents=True)

    task = ApkTask(
        task_id="20260610_120000",
        index=0,
        url="http://example/apk",
        status=ApkTaskStatus.FAILED,
    )
    manifest = BatchManifest(batch_root=batch_root, devices=["dev1"], tasks=[task])
    manifest.save()

    runtime = TaskRuntime(
        task_id="20260610_120000",
        index=0,
        serial="dev1",
        apk_url="http://example/apk",
        batch_root=batch_root,
        task_cache_dir=task_dir,
        gid="16914",
    )
    TaskRuntimeRegistry.register(runtime)

    archived, failed = cleanup_batch_workspace(
        batch_root,
        manifest,
        run_outputs_dir=run_out,
    )
    assert not failed
    assert batch_root not in [Path(p) for p in archived]
    assert (deliverable / "batch_manifest.json").is_file()
    payload = json.loads((deliverable / "batch_manifest.json").read_text(encoding="utf-8"))
    assert payload["devices"] == ["dev1"]
    assert not batch_root.exists()
    TaskRuntimeRegistry.clear()


def test_archive_skips_when_no_deliverable(tmp_path: Path) -> None:
    batch_root = tmp_path / "batch_x"
    batch_root.mkdir()
    task = ApkTask(task_id="missing", index=0, url="")
    manifest = BatchManifest(batch_root=batch_root, devices=[], tasks=[task])
    manifest.save()
    assert archive_batch_manifest(manifest, tmp_path) == []
