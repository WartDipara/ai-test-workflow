from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from game_agent.models.failure_report import FailureDiagnosisReport
from game_agent.models.settings import AppConfig
from game_agent.services.failure_report import (
    ATTEMPT_FAILURE_REPORT_JSON,
    ATTEMPT_FAILURE_REPORT_MD,
)

_CORE_DELIVERABLE_FILES = (
    "ai_analysis_report.txt",
    ATTEMPT_FAILURE_REPORT_MD,
    ATTEMPT_FAILURE_REPORT_JSON,
    "process.log",
    "deploy.log",
    "pipeline_trace.jsonl",
)


def build_in_game_play_summary(graph_state: dict | None) -> dict | None:
    if not graph_state:
        return None
    if not graph_state.get("in_game_play_completed") and not graph_state.get("in_game_confirmed"):
        return None
    duration = graph_state.get("in_game_play_duration_s")
    if duration is None:
        duration = 0
    return {
        "mode": str(graph_state.get("in_game_mode") or "smoke"),
        "duration_s": int(duration),
        "rounds": int(
            graph_state.get("in_game_play_rounds") or graph_state.get("in_game_agent_rounds") or 0
        ),
        "chains_built": int(graph_state.get("in_game_play_chains_built") or 0),
        "steps_executed": int(graph_state.get("in_game_play_steps_executed") or 0),
        "replans": int(graph_state.get("in_game_behavior_replan_count") or 0),
        "scene_memory_hits": int(graph_state.get("scene_memory_hits") or 0),
        "scene_memory_learns": int(graph_state.get("scene_memory_learns") or 0),
        "completed": bool(
            graph_state.get("in_game_play_completed") or graph_state.get("in_game_confirmed")
        ),
    }


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


def _resolve_failure_deliverable_files(app_config: AppConfig | None) -> tuple[str, ...]:
    if app_config is None:
        return _CORE_DELIVERABLE_FILES
    from game_agent.external_services.manager import ExternalServiceManager

    return ExternalServiceManager(app_config).failure_deliverable_files()


def publish_core_success_deliverable(
    deliverable: RunDeliverablePaths,
    *,
    winning_artifact_root: Path,
    winning_retry: int,
    total_attempts: int,
    package_name: str,
    source_apk: Path | None,
    install_apk: Path | None,
    in_game_confirmed: bool = True,
    session_restarts: int = 0,
    external_services: dict | None = None,
    in_game_play: dict | None = None,
) -> None:
    """成功：核心测试结果（是否进入游戏），不依赖 GameTurbo merged config。"""
    payload: dict = {
        "success": True,
        "gid": deliverable.gid,
        "task_id": deliverable.task_id,
        "winning_retry": winning_retry,
        "total_attempts": total_attempts,
        "package_name": package_name,
        "source_apk": str(source_apk.resolve()) if source_apk else None,
        "install_apk": str(install_apk.resolve()) if install_apk else None,
        "in_game_confirmed": in_game_confirmed,
        "winning_artifact": str(winning_artifact_root.resolve()),
        "session_restarts": session_restarts,
        "external_services": external_services or {},
        "finished_at": datetime.now(tz=UTC).isoformat(),
    }
    if in_game_play:
        payload["in_game_play"] = in_game_play
    _write_json(
        deliverable.root / "result.json",
        payload,
    )


def publish_success_deliverable(
    deliverable: RunDeliverablePaths,
    *,
    game_config_path: Path,
    winning_artifact_root: Path,
    winning_retry: int,
    total_attempts: int,
    session_restarts: int = 0,
    external_evidence: dict | None = None,
) -> Path:
    """成功：产出通过验证的 GameTurbo 合并配置（.gameturbo_merged.json 副本）。"""
    passed_name = game_config_path.name
    passed_config = deliverable.root / passed_name
    shutil.copy2(game_config_path, passed_config)

    payload: dict = {
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
        "finished_at": datetime.now(tz=UTC).isoformat(),
    }
    if external_evidence:
        payload["external_evidence"] = external_evidence

    _write_json(
        deliverable.root / "result.json",
        payload,
    )
    return passed_config


def publish_failure_deliverable(
    deliverable: RunDeliverablePaths,
    *,
    attempt_artifact_roots: list[tuple[int, Path]],
    last_reason: str,
    max_retries: int,
    ai_report: FailureDiagnosisReport | None = None,
    error_code: str = "",
    app_config: AppConfig | None = None,
) -> None:
    """失败：仅产出失败说明与各轮关联日志，不复制游戏配置 JSON。"""
    from game_agent.external_services.gameturbo.deliverables import (
        DEFAULT_OUTPUT_NAME,
        GAMETURBO_LOG_NAME,
    )
    from game_agent.external_services.manager import ExternalServiceManager

    deliverable_files = _resolve_failure_deliverable_files(app_config)
    session_log_glob = (
        ExternalServiceManager(app_config).failure_session_log_glob()
        if app_config is not None
        else None
    )
    gameturbo_enabled = (
        ExternalServiceManager(app_config).gameturbo_enabled()
        if app_config is not None
        else False
    )

    attempts_dir = deliverable.root / "attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)

    attempt_summaries: list[dict] = []
    for retry_no, artifact_root in attempt_artifact_roots:
        attempt_name = artifact_root.name
        attempt_dst = attempts_dir / attempt_name
        attempt_dst.mkdir(parents=True, exist_ok=True)

        for filename in deliverable_files:
            _copy_if_exists(artifact_root / filename, attempt_dst / filename)

        audit_src = artifact_root / "audit"
        if audit_src.is_dir():
            _copy_tree_if_exists(audit_src, attempt_dst / "audit")

        executor_src = artifact_root / "executor"
        if executor_src.is_dir():
            _copy_tree_if_exists(executor_src, attempt_dst / "executor")

        for png in sorted(artifact_root.glob("monitor_screen*.png")):
            _copy_if_exists(png, attempt_dst / png.name)
        for png in sorted(artifact_root.glob("entry_detect*.png")):
            _copy_if_exists(png, attempt_dst / png.name)
        if session_log_glob:
            for log in sorted(artifact_root.glob(session_log_glob)):
                _copy_if_exists(log, attempt_dst / log.name)

        summary: dict = {
            "retry": retry_no,
            "artifact": str(artifact_root.resolve()),
            "output_dir": str(attempt_dst.resolve()),
            "has_ai_report": (artifact_root / "ai_analysis_report.txt").is_file(),
            "has_attempt_failure_report": (
                artifact_root / ATTEMPT_FAILURE_REPORT_MD
            ).is_file(),
        }
        if gameturbo_enabled:
            summary["has_gameturbo_log"] = (artifact_root / GAMETURBO_LOG_NAME).is_file()
            summary["has_domain_analysis"] = (
                artifact_root / DEFAULT_OUTPUT_NAME
            ).is_file()
        attempt_summaries.append(summary)

    _write_json(
        deliverable.root / "result.json",
        {
            "success": False,
            "gid": deliverable.gid,
            "task_id": deliverable.task_id,
            "package_name": None,
            "install_apk": None,
            "in_game_confirmed": False,
            "error_code": error_code or None,
            "retryable": error_code.startswith("E2") if error_code else None,
            "max_retries": max_retries,
            "total_attempts": len(attempt_artifact_roots),
            "last_reason": last_reason[:4000],
            "attempts": attempt_summaries,
            "finished_at": datetime.now(tz=UTC).isoformat(),
            "note": "No game config produced. See failure_report.md first, then logs under attempts/.",
            "failure_report_md": str((deliverable.root / "failure_report.md").resolve())
            if ai_report is not None
            else None,
        },
    )

    summary_lines = [
        "# Test failure summary",
        "",
        f"- gid: {deliverable.gid}",
        f"- task_id: {deliverable.task_id}",
        f"- attempts: {len(attempt_artifact_roots)} / {max_retries}",
        f"- last reason: {last_reason}",
        "",
        "## Attempt artifacts",
    ]
    for item in attempt_summaries:
        summary_lines.append(
            f"- attempt {item['retry']}: {item['output_dir']}",
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
