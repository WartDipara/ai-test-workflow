from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from game_agent.models.failure_report import FailureDiagnosisReport
from game_agent.services.failure_report import (
    ATTEMPT_FAILURE_REPORT_JSON,
    ATTEMPT_FAILURE_REPORT_MD,
)
from game_agent.utils.gameturbo_log_domain_extract import DEFAULT_OUTPUT_NAME

_DELIVERABLE_FILES = (
    "gameturbo.log",
    DEFAULT_OUTPUT_NAME,
    "ai_analysis_report.txt",
    ATTEMPT_FAILURE_REPORT_MD,
    ATTEMPT_FAILURE_REPORT_JSON,
    "process.log",
    "deploy.log",
)


@dataclass(frozen=True, slots=True)
class RunDeliverablePaths:
    task_id: str
    gid: str
    root: Path


def new_task_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def task_output_dir(base_dir: Path, gid: str, task_id: str) -> Path:
    safe_gid = gid.strip() or "unknown"
    return (base_dir / f"{safe_gid}_{task_id}").resolve()


def create_task_output_dir(base_dir: Path, gid: str, task_id: str) -> RunDeliverablePaths:
    root = task_output_dir(base_dir, gid, task_id)
    root.mkdir(parents=True, exist_ok=True)
    return RunDeliverablePaths(task_id=task_id, gid=gid, root=root)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.is_file():
        shutil.copy2(src, dst)


def _copy_tree_if_exists(src: Path, dst: Path) -> None:
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def publish_success_deliverable(
    deliverable: RunDeliverablePaths,
    *,
    game_config_path: Path,
    winning_artifact_root: Path,
    winning_retry: int,
    total_attempts: int,
    session_restarts: int = 0,
) -> Path:
    """成功：产出通过验证的 GameTurbo 合并配置（.gameturbo_merged.json 副本）。"""
    passed_name = game_config_path.name
    passed_config = deliverable.root / passed_name
    shutil.copy2(game_config_path, passed_config)

    _write_json(
        deliverable.root / "result.json",
        {
            "success": True,
            "gid": deliverable.gid,
            "task_id": deliverable.task_id,
            "winning_retry": winning_retry,
            "total_attempts": total_attempts,
            "game_config": str(passed_config),
            "merged_config": str(passed_config),
            "source_config": str(game_config_path.resolve()),
            "source_merged_config": str(game_config_path.resolve()),
            "winning_artifact": str(winning_artifact_root.resolve()),
            "session_restarts": session_restarts,
            "finished_at": datetime.now(tz=timezone.utc).isoformat(),
        },
    )
    return passed_config


def publish_failure_deliverable(
    deliverable: RunDeliverablePaths,
    *,
    attempt_artifact_roots: list[tuple[int, Path]],
    last_reason: str,
    max_retries: int,
    ai_report: FailureDiagnosisReport | None = None,
) -> None:
    """失败：仅产出失败说明与各轮关联日志，不复制游戏配置 JSON。"""
    attempts_dir = deliverable.root / "attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)

    attempt_summaries: list[dict] = []
    for retry_no, artifact_root in attempt_artifact_roots:
        attempt_name = artifact_root.name
        attempt_dst = attempts_dir / attempt_name
        attempt_dst.mkdir(parents=True, exist_ok=True)

        for filename in _DELIVERABLE_FILES:
            _copy_if_exists(artifact_root / filename, attempt_dst / filename)

        audit_src = artifact_root / "audit"
        if audit_src.is_dir():
            _copy_tree_if_exists(audit_src, attempt_dst / "audit")

        keywizard_src = artifact_root / "keywizard"
        if keywizard_src.is_dir():
            _copy_tree_if_exists(keywizard_src, attempt_dst / "keywizard")

        for png in sorted(artifact_root.glob("monitor_screen*.png")):
            _copy_if_exists(png, attempt_dst / png.name)
        for png in sorted(artifact_root.glob("entry_detect*.png")):
            _copy_if_exists(png, attempt_dst / png.name)
        for log in sorted(artifact_root.glob("gameturbo_session_*.log")):
            _copy_if_exists(log, attempt_dst / log.name)

        attempt_summaries.append(
            {
                "retry": retry_no,
                "artifact": str(artifact_root.resolve()),
                "output_dir": str(attempt_dst.resolve()),
                "has_gameturbo_log": (artifact_root / "gameturbo.log").is_file(),
                "has_domain_analysis": (artifact_root / DEFAULT_OUTPUT_NAME).is_file(),
                "has_ai_report": (artifact_root / "ai_analysis_report.txt").is_file(),
                "has_attempt_failure_report": (
                    artifact_root / ATTEMPT_FAILURE_REPORT_MD
                ).is_file(),
            },
        )

    _write_json(
        deliverable.root / "result.json",
        {
            "success": False,
            "gid": deliverable.gid,
            "task_id": deliverable.task_id,
            "max_retries": max_retries,
            "total_attempts": len(attempt_artifact_roots),
            "last_reason": last_reason[:4000],
            "attempts": attempt_summaries,
            "finished_at": datetime.now(tz=timezone.utc).isoformat(),
            "note": "未产出游戏配置文件；请优先查看 failure_report.md，其次 attempts/ 下各轮日志。",
            "failure_report_md": str((deliverable.root / "failure_report.md").resolve())
            if ai_report is not None
            else None,
        },
    )

    summary_lines = [
        "# 测试失败摘要",
        "",
        f"- gid: {deliverable.gid}",
        f"- task_id: {deliverable.task_id}",
        f"- 尝试次数: {len(attempt_artifact_roots)} / {max_retries}",
        f"- 最后原因: {last_reason}",
        "",
        "## 各轮产物",
    ]
    for item in attempt_summaries:
        summary_lines.append(
            f"- 第 {item['retry']} 轮: {item['output_dir']}",
        )
    (deliverable.root / "failure_summary.md").write_text(
        "\n".join(summary_lines) + "\n",
        encoding="utf-8",
    )

    if ai_report is not None:
        report_path = deliverable.root / "failure_report.md"
        report_path.write_text(
            ai_report.to_markdown(
                gid=deliverable.gid,
                task_id=deliverable.task_id,
                last_reason=last_reason,
            ),
            encoding="utf-8",
        )
        _write_json(
            deliverable.root / "failure_report.json",
            ai_report.model_dump(),
        )
