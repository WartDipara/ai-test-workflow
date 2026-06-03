"""
任务收尾：归档 logs/、生成 final_logs.log（仅执行流）、清理 artifacts/retry_*。

编排器在任务成功或最终失败时会自动调用；也可手动执行：

  python -m game_agent.tools.finalize_task \\
    --deliverable run_outputs/16173_20260528_120000 \\
    --artifacts artifacts/retry_1_20260528_100000 [...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from game_agent.config.loader import load_app_config
from game_agent.services.run_deliverable import RunDeliverablePaths
from game_agent.services.task_finalize import finalize_task_deliverable


def main() -> int:
    parser = argparse.ArgumentParser(description="Build final_logs.log and cleanup artifacts")
    parser.add_argument(
        "--deliverable",
        type=Path,
        required=True,
        help="run_outputs/{gid}_{task_id} directory",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        nargs="*",
        default=[],
        help="artifact retry directories to remove",
    )
    parser.add_argument(
        "--success",
        action="store_true",
        help="Mark task as successful in final_logs header",
    )
    parser.add_argument(
        "--last-reason",
        default="manual finalize",
        help="Failure/summary reason line",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Only write final_logs.log, do not delete artifacts",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/settings.yaml"),
        help="Optional config to read gid if result.json missing",
    )
    args = parser.parse_args()

    deliverable_root = args.deliverable.resolve()
    if not deliverable_root.is_dir():
        print(f"Deliverable directory not found: {deliverable_root}", file=sys.stderr)
        return 2

    gid = "unknown"
    task_id = deliverable_root.name
    result_path = deliverable_root / "result.json"
    if result_path.is_file():
        data = json.loads(result_path.read_text(encoding="utf-8"))
        gid = str(data.get("gid", gid))
        task_id = str(data.get("task_id", task_id))
    elif args.config.is_file():
        cfg = load_app_config(args.config)
        gid = (cfg.gameturbo.gid or "unknown").strip() or "unknown"

    attempt_records: list[tuple[int, Path]] = []
    for i, art in enumerate(args.artifacts, start=1):
        attempt_records.append((i, art.resolve()))

    deliverable = RunDeliverablePaths(task_id=task_id, gid=gid, root=deliverable_root)
    artifacts_dir = (
        attempt_records[0][1].parent.resolve()
        if attempt_records
        else Path("artifacts").resolve()
    )

    outcome = finalize_task_deliverable(
        deliverable,
        success=args.success,
        max_retries=max(1, len(attempt_records)),
        winning_retry=1 if args.success else 0,
        last_reason=args.last_reason,
        attempt_records=attempt_records,
        preprocess_record=None,
        preprocessing_enabled=False,
        cleanup_artifacts=not args.no_cleanup,
        artifacts_dir=artifacts_dir,
    )
    print(f"final_logs: {outcome.final_log_path}")
    print(f"execution_manifest: {outcome.execution_manifest_path}")
    print(f"artifacts removed: {len(outcome.artifacts_removed)}")
    if outcome.artifacts_failed:
        print(f"cleanup errors: {outcome.artifacts_failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
