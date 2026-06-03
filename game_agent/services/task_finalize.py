from __future__ import annotations

import json
import logging
import os
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from game_agent.modules.preprocessing.preprocessor import PreprocessResult
from game_agent.services.run_deliverable import RunDeliverablePaths

logger = logging.getLogger(__name__)

FINAL_LOG_NAME = "final_logs.log"
TASK_JOURNAL_NAME = "task_journal.jsonl"
_DEFAULT_SECTION_BYTES = 400_000


@dataclass(slots=True)
class TaskFinalizeResult:
    final_log_path: Path
    artifacts_removed: list[str]
    artifacts_failed: list[str]


class TaskRunJournal:
    """Task-level timeline (under run_outputs/{gid}_{task_id}/)."""

    def __init__(self, deliverable_root: Path) -> None:
        self._path = (deliverable_root / TASK_JOURNAL_NAME).resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, phase: str, event: str, **extra: Any) -> None:
        row = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "phase": phase,
            "event": event,
            **extra,
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("写入 task_journal 失败: %s", e)


def _now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append_section(lines: list[str], title: str) -> None:
    lines.append("")
    lines.append("=" * 80)
    lines.append(title)
    lines.append("=" * 80)


def _read_text_capped(path: Path, max_bytes: int) -> str:
    if not path.is_file():
        return f"(missing: {path.name})"
    data = path.read_bytes()
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace")
    head = data[:max_bytes].decode("utf-8", errors="replace")
    return f"{head}\n…[truncated, file size {len(data)} bytes, cap {max_bytes}]\n"


def _format_audit_events(path: Path, max_bytes: int) -> str:
    if not path.is_file():
        return "(no audit/events.jsonl)"
    out: list[str] = []
    used = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            used += len(line) + 1
            continue
        ts = ev.get("ts", "")
        kind = ev.get("kind", ev.get("type", "event"))
        phase = ev.get("phase", "")
        msg = ev.get("message", ev.get("text", ""))[:500]
        out.append(f"{ts} | {kind} | {phase} | {msg}")
        used += 200
        if used > max_bytes:
            out.append("…[audit events truncated]")
            break
    return "\n".join(out) if out else "(empty audit/events.jsonl)"


def _format_jsonl(path: Path, max_bytes: int) -> str:
    if not path.is_file():
        return f"(missing: {path.name})"
    return _read_text_capped(path, max_bytes)


def _preprocess_section(record: PreprocessResult | None, enabled: bool) -> list[str]:
    lines = ["", "Phase 0 — Preprocessing (once per task)"]
    lines.append(f"  enabled: {enabled}")
    if not enabled:
        lines.append("  (skipped)")
        return lines
    if record is None:
        lines.append("  (no result recorded)")
        return lines
    lines.append(f"  ok: {record.ok}")
    lines.append(f"  message: {record.message}")
    if record.source_apk:
        lines.append(f"  source_apk: {record.source_apk}")
    if record.processed_apk:
        lines.append(f"  processed_apk: {record.processed_apk}")
    if record.abis_kept:
        lines.append(f"  abis_kept: {', '.join(record.abis_kept)}")
    if record.abis_removed:
        lines.append(f"  abis_removed: {', '.join(record.abis_removed)}")
    return lines


def build_final_logs(
    deliverable: RunDeliverablePaths,
    *,
    success: bool,
    max_retries: int,
    winning_retry: int,
    last_reason: str,
    attempt_records: list[tuple[int, Path]],
    preprocess_record: PreprocessResult | None,
    preprocessing_enabled: bool,
    modules_summary: dict[str, Any] | None = None,
    max_section_bytes: int = _DEFAULT_SECTION_BYTES,
) -> Path:
    """Assemble final_logs.log under the task deliverable directory."""
    out_path = deliverable.root / FINAL_LOG_NAME
    lines: list[str] = [
        "GAME AGENT TASK — FINAL LOG",
        f"generated_at: {_now_local()}",
        f"gid: {deliverable.gid}",
        f"task_id: {deliverable.task_id}",
        f"success: {success}",
        f"winning_retry: {winning_retry}",
        f"max_retries: {max_retries}",
        f"total_attempts: {len(attempt_records)}",
        f"last_reason: {last_reason[:2000]}",
        f"deliverable_dir: {deliverable.root}",
    ]

    if modules_summary:
        lines.append(f"modules: {json.dumps(modules_summary, ensure_ascii=False)}")

    journal_path = deliverable.root / TASK_JOURNAL_NAME
    _append_section(lines, "Task journal (orchestrator milestones)")
    lines.append(_read_text_capped(journal_path, max_section_bytes))

    _append_section(lines, "Preprocessing")
    lines.extend(_preprocess_section(preprocess_record, preprocessing_enabled))

    for retry_no, artifact_root in attempt_records:
        _append_section(
            lines,
            f"Attempt {retry_no} — {artifact_root.name}",
        )
        lines.append(f"artifact_root: {artifact_root}")

        audit_events = artifact_root / "audit" / "events.jsonl"
        lines.append("")
        lines.append("--- Phase timeline (audit/events.jsonl) ---")
        lines.append(_format_audit_events(audit_events, max_section_bytes // 4))

        for label, rel in (
            ("process.log", "process.log"),
            ("pipeline_trace.jsonl", "pipeline_trace.jsonl"),
            ("deploy.log", "deploy.log"),
            ("gameturbo.log (tail)", "gameturbo.log"),
        ):
            p = artifact_root / rel
            lines.append("")
            lines.append(f"--- {label} ---")
            if rel == "gameturbo.log" and p.is_file():
                raw = p.read_bytes()
                tail = raw[-min(len(raw), max_section_bytes // 2) :]
                text = tail.decode("utf-8", errors="replace")
                if len(raw) > len(tail):
                    text = f"…[last {len(tail)} bytes of {len(raw)}]\n" + text
                lines.append(text)
            else:
                lines.append(_read_text_capped(p, max_section_bytes // 3))

        attempt_report = artifact_root / "attempt_failure_report.md"
        if attempt_report.is_file():
            lines.append("")
            lines.append("--- attempt_failure_report.md ---")
            lines.append(_read_text_capped(attempt_report, max_section_bytes // 4))

    _append_section(lines, "Deliverable files (run_outputs)")
    for p in sorted(deliverable.root.iterdir()) if deliverable.root.is_dir() else []:
        if p.name in (FINAL_LOG_NAME, TASK_JOURNAL_NAME):
            continue
        if p.is_file():
            lines.append(f"  file: {p.name} ({p.stat().st_size} bytes)")
        elif p.is_dir():
            lines.append(f"  dir:  {p.name}/")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("已写入 %s (%d bytes)", out_path, out_path.stat().st_size)
    return out_path


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

    # 兜底：删除 artifacts 根下名称匹配且未在 attempt_records 中列出的 retry_/run_ 目录
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


def archive_attempt_pipeline_traces(
    deliverable_root: Path,
    attempt_records: list[tuple[int, Path]],
) -> None:
    """在删除 artifacts 前，将各轮 pipeline_trace.jsonl 复制到 run_outputs/attempts/。"""
    if not attempt_records:
        return
    attempts_dir = deliverable_root / "attempts"
    for _retry_no, artifact_root in attempt_records:
        src = artifact_root / "pipeline_trace.jsonl"
        if not src.is_file():
            continue
        dst_dir = attempts_dir / artifact_root.name
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / "pipeline_trace.jsonl"
        try:
            shutil.copy2(src, dst)
            logger.debug("已归档 pipeline trace: %s", dst)
        except OSError as e:
            logger.warning("归档 pipeline trace 失败 %s: %s", src, e)


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
    Write final_logs.log to run_outputs, then delete attempt artifact trees.
    Call after publish_success/failure_deliverable.
    """
    journal = TaskRunJournal(deliverable.root)
    journal.log("finalize", "start", success=success)

    final_path = build_final_logs(
        deliverable,
        success=success,
        max_retries=max_retries,
        winning_retry=winning_retry,
        last_reason=last_reason,
        attempt_records=attempt_records,
        preprocess_record=preprocess_record,
        preprocessing_enabled=preprocessing_enabled,
        modules_summary=modules_summary,
    )

    archive_attempt_pipeline_traces(deliverable.root, attempt_records)

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

    footer_lines = [
        "",
        "=" * 80,
        "Post-finalize",
        "=" * 80,
        f"artifacts_removed: {len(removed)}",
    ]
    for p in removed:
        footer_lines.append(f"  - {p}")
    if failed:
        footer_lines.append(f"artifacts_cleanup_errors: {failed}")
    with final_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(footer_lines) + "\n")

    journal.log(
        "finalize",
        "completed",
        success=success,
        final_log=str(final_path),
        artifacts_removed=len(removed),
        artifacts_failed=len(failed),
    )

    result_path = deliverable.root / "result.json"
    if result_path.is_file():
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
            data["final_logs"] = str(final_path.resolve())
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
        artifacts_removed=removed,
        artifacts_failed=failed,
    )
