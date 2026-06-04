from __future__ import annotations

import json
import logging
import os
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from game_agent.modules.preprocessing.preprocessor import PreprocessResult
from game_agent.services.execution_log_bundle import (
    archive_attempt_logs,
    build_final_logs,
    write_execution_manifest,
)
from game_agent.services.run_deliverable import RunDeliverablePaths

logger = logging.getLogger(__name__)

TASK_JOURNAL_NAME = "task_journal.jsonl"


@dataclass(slots=True)
class TaskFinalizeResult:
    final_log_path: Path
    execution_manifest_path: Path
    artifacts_removed: list[str]
    artifacts_failed: list[str]


class TaskRunJournal:
    """Task-level timeline (under run_outputs/{gid}_{task_id}/)."""

    def __init__(self, deliverable_root: Path) -> None:
        self._path = (deliverable_root / TASK_JOURNAL_NAME).resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, phase: str, event: str, **extra: Any) -> None:
        row = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "phase": phase,
            "event": event,
            **extra,
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("写入 task_journal 失败: %s", e)


def _chmod_writable_and_retry(func, path: str, exc_info) -> None:
    if not os.path.exists(path):
        return
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def detach_process_log_handlers_for_roots(artifact_roots: list[Path]) -> int:
    """从 root logger 移除指向各 attempt process.log 的 FileHandler。"""
    targets = {
        (root.resolve() / "process.log").resolve()
        for root in artifact_roots
        if root.is_dir()
    }
    root_logger = logging.getLogger()
    detached = 0
    for handler in list(root_logger.handlers):
        if not isinstance(handler, logging.FileHandler):
            continue
        try:
            log_file = Path(handler.baseFilename).resolve()
        except (OSError, AttributeError):
            continue
        if log_file not in targets:
            continue
        try:
            handler.flush()
            handler.close()
        except OSError:
            pass
        if handler in root_logger.handlers:
            root_logger.removeHandler(handler)
        detached += 1
    return detached


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, onerror=_chmod_writable_and_retry)


def cleanup_attempt_artifacts(artifact_roots: list[Path]) -> tuple[list[str], list[str]]:
    """Remove per-attempt artifact directories after deliverable + final_logs are ready."""
    detach_process_log_handlers_for_roots(artifact_roots)
    removed: list[str] = []
    failed: list[str] = []
    seen: set[Path] = set()
    for root in artifact_roots:
        path = root.resolve()
        if path in seen or not path.is_dir():
            continue
        seen.add(path)
        try:
            _remove_tree(path)
            removed.append(str(path))
            logger.info("已清理 artifacts: %s", path)
        except OSError as e:
            failed.append(f"{path}: {e}")
            logger.warning("清理 artifacts 失败: %s — %s", path, e)
    return removed, failed


def cleanup_task_artifacts(
    artifacts_dir: Path,
    attempt_records: list[tuple[int, Path]],
) -> tuple[list[str], list[str]]:
    """
    任务结束后清理本轮所有 retry_*/run_* 中间目录（以 attempt_records 为准）。
    """
    roots = [artifact_root for _, artifact_root in attempt_records]
    removed, failed = cleanup_attempt_artifacts(roots)

    if not artifacts_dir.is_dir():
        return removed, failed

    recorded = {p.resolve() for p in roots}
    for child in sorted(artifacts_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.resolve() in recorded:
            continue
        if not (child.name.startswith("retry_") or child.name.startswith("run_")):
            continue
        try:
            _remove_tree(child)
            removed.append(str(child.resolve()))
            logger.info("已清理遗留 artifacts: %s", child)
        except OSError as e:
            failed.append(f"{child}: {e}")
    return removed, failed


def finalize_task_deliverable(
    deliverable: RunDeliverablePaths,
    *,
    success: bool,
    max_retries: int,
    winning_retry: int,
    last_reason: str,
    attempt_records: list[tuple[int, Path]],
    preprocess_record: PreprocessResult | None,
    preprocessing_enabled: bool,
    cleanup_artifacts: bool = True,
    artifacts_dir: Path | None = None,
    modules_summary: dict[str, Any] | None = None,
) -> TaskFinalizeResult:
    """
    1. 归档完整执行日志到 logs/、分析报告到 reports/
    2. 生成 final_logs.log（仅执行流，不含 Markdown 失败报告）
    3. 写入 execution_manifest.json
    4. 删除 artifacts/retry_*
    """
    del modules_summary  # 已写入 task_journal / result.json，不塞进 final_logs

    journal = TaskRunJournal(deliverable.root)
    journal.log("finalize", "start", success=success)

    archives = archive_attempt_logs(deliverable.root, attempt_records)

    final_path = build_final_logs(
        deliverable,
        success=success,
        max_retries=max_retries,
        winning_retry=winning_retry,
        last_reason=last_reason,
        attempt_records=attempt_records,
        preprocess_record=preprocess_record,
        preprocessing_enabled=preprocessing_enabled,
        archives=archives,
    )

    removed: list[str] = []
    failed: list[str] = []
    if cleanup_artifacts and attempt_records:
        base = artifacts_dir
        if base is None and attempt_records:
            base = attempt_records[0][1].parent
        if base is not None:
            removed, failed = cleanup_task_artifacts(base, attempt_records)
        else:
            removed, failed = cleanup_attempt_artifacts(
                [p for _, p in attempt_records],
            )

    manifest_path = write_execution_manifest(
        deliverable.root,
        final_log_path=final_path,
        success=success,
        archives=archives,
        artifacts_removed=removed,
        artifacts_failed=failed,
    )

    with final_path.open("a", encoding="utf-8") as out:
        out.write("\n")
        out.write("-" * 72 + "\n")
        out.write("  Post-finalize\n")
        out.write("-" * 72 + "\n")
        out.write(f"  artifacts_removed: {len(removed)}\n")
        for p in removed:
            out.write(f"    {p}\n")
        if failed:
            out.write(f"  artifacts_cleanup_errors: {failed}\n")

    journal.log(
        "finalize",
        "completed",
        success=success,
        final_log=str(final_path),
        execution_manifest=str(manifest_path),
        artifacts_removed=len(removed),
        artifacts_failed=len(failed),
    )

    result_path = deliverable.root / "result.json"
    if result_path.is_file():
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
            data["final_logs"] = str(final_path.resolve())
            data["execution_manifest"] = str(manifest_path.resolve())
            from game_agent.models.run_failure import parse_error_code_from_text

            ec = parse_error_code_from_text(str(data.get("last_reason", "")))
            if ec:
                data["error_code"] = ec
            data["artifacts_cleaned"] = removed
            if failed:
                data["artifacts_cleanup_errors"] = failed
            result_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("更新 result.json 失败: %s", e)

    return TaskFinalizeResult(
        final_log_path=final_path,
        execution_manifest_path=manifest_path,
        artifacts_removed=removed,
        artifacts_failed=failed,
    )
