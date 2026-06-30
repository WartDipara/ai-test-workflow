from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from game_agent.external_services.manager import (
    CORE_ANALYSIS_LOG_FILES,
    CORE_EXECUTION_LOG_FILES,
    ExecutionLogArchivePlan,
    core_execution_log_archive_plan,
)
from game_agent.modules.preprocessing.preprocessor import PreprocessResult
from game_agent.services.run_deliverable import RunDeliverablePaths

logger = logging.getLogger(__name__)

FINAL_LOG_NAME = "final_logs.log"
EXECUTION_MANIFEST_NAME = "execution_manifest.json"
LOGS_SUBDIR = "logs"
REPORTS_SUBDIR = "reports"

EXECUTION_FILE_NAMES: tuple[str, ...] = CORE_EXECUTION_LOG_FILES

ANALYSIS_FILE_NAMES: tuple[str, ...] = CORE_ANALYSIS_LOG_FILES

# 默认单文件内联上限（超出则只在 final_logs 中引用 logs/ 路径）
DEFAULT_INLINE_MAX_BYTES = 12 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AttemptLogArchive:
    retry_no: int
    artifact_name: str
    logs_dir: Path
    reports_dir: Path | None
    files: dict[str, int]  # name -> bytes


def _now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write_banner(out: TextIO, title: str, *, width: int = 72, char: str = "=") -> None:
    out.write(f"\n{char * width}\n")
    out.write(f"  {title}\n")
    out.write(f"{char * width}\n")


def _write_subbanner(out: TextIO, title: str) -> None:
    out.write(f"\n{'-' * 72}\n")
    out.write(f"  {title}\n")
    out.write(f"{'-' * 72}\n")


def _format_task_journal(path: Path) -> str:
    if not path.is_file():
        return "  (no task_journal.jsonl)\n"
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            lines.append(f"  {raw[:200]}")
            continue
        ts = str(row.get("ts", ""))[:19].replace("T", " ")
        phase = row.get("phase", "")
        event = row.get("event", "")
        extra = {
            k: row[k]
            for k in sorted(row)
            if k not in ("ts", "phase", "event")
        }
        suffix = ""
        if extra:
            parts = [f"{k}={extra[k]!r}" for k in list(extra)[:6]]
            suffix = " | " + ", ".join(parts)
        lines.append(f"  [{ts}] {phase}.{event}{suffix}")
    return "\n".join(lines) + "\n"


def _format_audit_events_execution(path: Path) -> str:
    """仅格式化 events.jsonl（执行时间线），不含 ai_trace.md 叙事。"""
    if not path.is_file():
        return "  (no audit/events.jsonl)\n"
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            lines.append(f"  {raw[:300]}")
            continue
        ts = str(ev.get("ts", ""))[:19].replace("T", " ")
        kind = ev.get("kind", ev.get("type", "event"))
        phase = ev.get("phase", "")
        parts = [f"[{ts}]", kind, phase]
        for key in ("tool", "round_id", "message", "note", "reason"):
            val = ev.get(key)
            if val:
                text = str(val).replace("\n", " ")[:400]
                parts.append(f"{key}={text}")
        lines.append("  " + " | ".join(parts))
    return "\n".join(lines) + "\n"


def _copy_if_exists(src: Path, dst: Path) -> int:
    if not src.is_file():
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst.stat().st_size


def archive_attempt_logs(
    deliverable_root: Path,
    attempt_records: list[tuple[int, Path]],
    *,
    archive_plan: ExecutionLogArchivePlan | None = None,
) -> list[AttemptLogArchive]:
    """将各轮执行日志完整拷贝到 run_outputs/logs/，分析报告到 reports/。"""
    plan = archive_plan or core_execution_log_archive_plan()
    archives: list[AttemptLogArchive] = []
    logs_root = deliverable_root / LOGS_SUBDIR
    reports_root = deliverable_root / REPORTS_SUBDIR

    for retry_no, artifact_root in attempt_records:
        name = artifact_root.name
        logs_dir = logs_root / name
        logs_dir.mkdir(parents=True, exist_ok=True)
        sizes: dict[str, int] = {}

        if plan.prepare_artifact is not None:
            plan.prepare_artifact(artifact_root)

        for fname in plan.execution_files:
            n = _copy_if_exists(artifact_root / fname, logs_dir / fname)
            if n:
                sizes[fname] = n

        if plan.session_log_glob:
            for session_log in sorted(artifact_root.glob(plan.session_log_glob)):
                n = _copy_if_exists(session_log, logs_dir / session_log.name)
                if n:
                    sizes[session_log.name] = n

        audit_src = artifact_root / "audit" / "events.jsonl"
        n = _copy_if_exists(audit_src, logs_dir / "audit_events.jsonl")
        if n:
            sizes["audit_events.jsonl"] = n

        exec_dir = artifact_root / "executor"
        if exec_dir.is_dir():
            dst_exec = logs_dir / "executor"
            if dst_exec.exists():
                shutil.rmtree(dst_exec)
            shutil.copytree(
                exec_dir,
                dst_exec,
                ignore=shutil.ignore_patterns("*.png", "*.jpg"),
            )
            sizes["executor/"] = sum(
                f.stat().st_size for f in dst_exec.rglob("*") if f.is_file()
            )

        reports_dir: Path | None = None
        report_files = [artifact_root / n for n in plan.analysis_files]
        if any(p.is_file() for p in report_files):
            reports_dir = reports_root / name
            reports_dir.mkdir(parents=True, exist_ok=True)
            for p in report_files:
                if p.is_file():
                    shutil.copy2(p, reports_dir / p.name)

        archives.append(
            AttemptLogArchive(
                retry_no=retry_no,
                artifact_name=name,
                logs_dir=logs_dir.resolve(),
                reports_dir=reports_dir.resolve() if reports_dir else None,
                files=sizes,
            ),
        )
        logger.info(
            "Archived execution logs attempt=%s -> %s (%d files)",
            retry_no,
            logs_dir.name,
            len(sizes),
        )
    return archives


def _inline_file(
    out: TextIO,
    *,
    label: str,
    src: Path,
    archived: Path | None,
    inline_max_bytes: int,
) -> None:
    if not src.is_file() and (archived is None or not archived.is_file()):
        out.write(f"  (missing {label})\n")
        return

    path = src if src.is_file() else archived
    assert path is not None and path.is_file()
    size = path.stat().st_size
    _write_subbanner(out, label)
    if size > inline_max_bytes:
        out.write(
            f"  size={size} bytes (exceeds inline cap {inline_max_bytes})\n"
            f"  full copy: {archived or path}\n",
        )
        return

    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.endswith("\n"):
        text += "\n"
    out.write(text)


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
    archives: list[AttemptLogArchive],
    inline_max_bytes: int = DEFAULT_INLINE_MAX_BYTES,
    archive_plan: ExecutionLogArchivePlan | None = None,
) -> Path:
    """
    生成 final_logs.log：仅执行过程（无 AI 失败报告 Markdown / 分析 JSON 正文）。
    超大文件在文内引用 logs/<attempt>/ 下的完整副本。
    """
    plan = archive_plan or core_execution_log_archive_plan()
    out_path = deliverable.root / FINAL_LOG_NAME
    journal_path = deliverable.root / "task_journal.jsonl"

    with out_path.open("w", encoding="utf-8") as out:
        _write_banner(out, "GAME AGENT — EXECUTION LOG (process trace only)")
        out.write(f"  generated: {_now_local()}\n")
        out.write(f"  gid: {deliverable.gid}\n")
        out.write(f"  task_id: {deliverable.task_id}\n")
        out.write(f"  success: {success}\n")
        out.write(f"  winning_retry: {winning_retry}\n")
        out.write(f"  max_retries: {max_retries}\n")
        out.write(f"  attempts: {len(attempt_records)}\n")
        if last_reason.strip():
            out.write(f"  last_reason: {last_reason.strip()[:500]}\n")
        out.write(
            f"  full log files: {deliverable.root / LOGS_SUBDIR}/\n"
            f"  analysis reports: {deliverable.root / REPORTS_SUBDIR}/ "
            "(not included below)\n",
        )

        _write_banner(out, "Task timeline (from task_journal.jsonl)", char="-")
        out.write(_format_task_journal(journal_path))

        _write_banner(out, "Preprocessing", char="-")
        out.write(f"  enabled: {preprocessing_enabled}\n")
        if preprocess_record:
            out.write(f"  ok: {preprocess_record.ok}\n")
            out.write(f"  message: {preprocess_record.message}\n")
            if preprocess_record.processed_apk:
                out.write(f"  apk: {preprocess_record.processed_apk}\n")

        archive_by_name = {a.artifact_name: a for a in archives}

        for retry_no, artifact_root in attempt_records:
            name = artifact_root.name
            arch = archive_by_name.get(name)
            logs_ref = arch.logs_dir if arch else deliverable.root / LOGS_SUBDIR / name

            _write_banner(out, f"Attempt {retry_no} — {name}")
            out.write(f"  artifact: {artifact_root}\n")
            out.write(f"  archived logs: {logs_ref}\n")
            if arch and arch.reports_dir:
                out.write(f"  analysis reports: {arch.reports_dir}\n")

            for fname in plan.execution_files:
                src = artifact_root / fname
                archived = logs_ref / fname if logs_ref else None
                _inline_file(
                    out,
                    label=fname,
                    src=src,
                    archived=archived,
                    inline_max_bytes=inline_max_bytes,
                )

            audit_src = artifact_root / "audit" / "events.jsonl"
            _write_subbanner(out, "audit/events.jsonl (execution timeline)")
            out.write(_format_audit_events_execution(audit_src))

        _write_banner(out, "End of execution log", char="-")
        out.write(f"  manifest: {deliverable.root / EXECUTION_MANIFEST_NAME}\n")

    logger.info("Wrote %s (%d bytes)", out_path, out_path.stat().st_size)
    return out_path


def write_execution_manifest(
    deliverable_root: Path,
    *,
    final_log_path: Path,
    success: bool,
    archives: list[AttemptLogArchive],
    artifacts_removed: list[str],
    artifacts_failed: list[str],
) -> Path:
    path = deliverable_root / EXECUTION_MANIFEST_NAME
    payload: dict[str, Any] = {
        "final_logs": str(final_log_path.resolve()),
        "success": success,
        "logs_dir": str((deliverable_root / LOGS_SUBDIR).resolve()),
        "reports_dir": str((deliverable_root / REPORTS_SUBDIR).resolve()),
        "attempts": [
            {
                "retry": a.retry_no,
                "artifact_name": a.artifact_name,
                "logs_dir": str(a.logs_dir),
                "reports_dir": str(a.reports_dir) if a.reports_dir else None,
                "files": a.files,
            }
            for a in archives
        ],
        "artifacts_cleaned": artifacts_removed,
        "artifacts_cleanup_errors": artifacts_failed,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
