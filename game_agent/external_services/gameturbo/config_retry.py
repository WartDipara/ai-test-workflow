from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from game_agent.external_services.gameturbo.models.config import GameTurboConfigPatch
from game_agent.external_services.gameturbo.config.apply import ConfigApplyResult

logger = logging.getLogger(__name__)

CONFIG_BACKUPS_DIR = "config_backups"
JOURNAL_FILE = "config_retry_journal.jsonl"
BASELINE_NAME = "gameturbo_baseline.json"


@dataclass
class ConfigRetryJournalEntry:
    """单次 Modify 阶段记录（写入 run_outputs）。"""

    failed_attempt: int
    next_attempt: int
    timestamp: str
    game_config_path: str
    restored_from: str | None
    backup_before_path: str
    backup_after_path: str | None
    patch_analysis: str
    patch_direct_patterns: list[str] = field(default_factory=list)
    patch_port_rules: list[dict[str, Any]] = field(default_factory=list)
    apply_summary: list[str] = field(default_factory=list)
    apply_changed: bool = False
    blocked_stage_hint: str = ""


def _backup_dir(deliverable_root: Path) -> Path:
    d = deliverable_root / CONFIG_BACKUPS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_baseline_copy(game_config_path: Path, deliverable_root: Path) -> Path:
    dst = _backup_dir(deliverable_root) / BASELINE_NAME
    if not dst.is_file():
        shutil.copy2(game_config_path, dst)
        logger.info("[ConfigRetry] Baseline config saved -> %s", dst)
    return dst


def backup_path_for_next_attempt(deliverable_root: Path, next_attempt: int) -> Path:
    return _backup_dir(deliverable_root) / f"before_attempt_{next_attempt}.json"


def after_patch_path(deliverable_root: Path, next_attempt: int) -> Path:
    return _backup_dir(deliverable_root) / f"after_patch_attempt_{next_attempt}.json"


def restore_before_new_patch(
    game_config_path: Path,
    deliverable_root: Path,
    *,
    failed_attempt: int,
) -> str | None:
    if failed_attempt < 2:
        return None

    restore_from = backup_path_for_next_attempt(deliverable_root, failed_attempt)
    if not restore_from.is_file():
        baseline = _backup_dir(deliverable_root) / BASELINE_NAME
        if baseline.is_file():
            restore_from = baseline
        else:
            logger.warning(
                "[ConfigRetry] 无 before_attempt_%d 或基线，跳过恢复",
                failed_attempt,
            )
            return None

    shutil.copy2(restore_from, game_config_path)
    logger.info(
        "[ConfigRetry] Restored config (reverted last patch) %s -> %s",
        restore_from.name,
        game_config_path,
    )
    return str(restore_from.resolve())


def backup_config_before_patch(
    game_config_path: Path,
    deliverable_root: Path,
    *,
    next_attempt: int,
    artifact_root: Path | None = None,
) -> Path:
    ensure_baseline_copy(game_config_path, deliverable_root)
    dst = backup_path_for_next_attempt(deliverable_root, next_attempt)
    shutil.copy2(game_config_path, dst)
    if artifact_root is not None:
        art_dst = artifact_root / f"game_config_before_attempt_{next_attempt}.json"
        shutil.copy2(game_config_path, art_dst)
    logger.info("[ConfigRetry] Pre-patch backup -> %s", dst)
    return dst


def backup_config_after_patch(
    game_config_path: Path,
    deliverable_root: Path,
    *,
    next_attempt: int,
    artifact_root: Path | None = None,
) -> Path:
    dst = after_patch_path(deliverable_root, next_attempt)
    shutil.copy2(game_config_path, dst)
    if artifact_root is not None:
        art_dst = artifact_root / f"game_config_after_patch_attempt_{next_attempt}.json"
        shutil.copy2(game_config_path, art_dst)
    logger.info("[ConfigRetry] Post-patch backup -> %s", dst)
    return dst


def append_journal_entry(deliverable_root: Path, entry: ConfigRetryJournalEntry) -> None:
    path = deliverable_root / JOURNAL_FILE
    line = json.dumps(asdict(entry), ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def load_journal_entries(deliverable_root: Path) -> list[ConfigRetryJournalEntry]:
    path = deliverable_root / JOURNAL_FILE
    if not path.is_file():
        return []
    out: list[ConfigRetryJournalEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            out.append(ConfigRetryJournalEntry(**data))
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Skip invalid journal line: %s", e)
    return out


def format_last_patch_for_executor(deliverable_root: Path) -> str:
    entries = load_journal_entries(deliverable_root)
    if not entries:
        return ""
    last = entries[-1]
    parts = [
        f"Last GameTurbo patch (after attempt {last.failed_attempt}, for attempt {last.next_attempt}):",
        f"  changed={last.apply_changed}; summary={last.apply_summary or ['none']}",
    ]
    if last.patch_direct_patterns:
        parts.append(f"  direct_patterns added: {last.patch_direct_patterns[:12]}")
    if last.patch_port_rules:
        parts.append(f"  port_rules: {len(last.patch_port_rules)} rule(s)")
    if last.patch_analysis:
        parts.append(f"  analysis: {last.patch_analysis[:600]}")
    if last.blocked_stage_hint:
        parts.append(f"  blocked_at: {last.blocked_stage_hint}")
    parts.append(
        "VERIFY on this run: confirm you pass the stage that failed last time "
        "(especially resource download / 资源下载 / update MB dialog) before assuming success.",
    )
    return "\n".join(parts)


from game_agent.i18n.stages import infer_blocked_stage as infer_blocked_stage


def prepare_modify_stage(
    game_config_path: Path,
    deliverable_root: Path,
    *,
    failed_attempt: int,
    artifact_root: Path | None,
    blocked_stage_hint: str = "",
) -> tuple[Path, str | None]:
    next_attempt = failed_attempt + 1
    restored = restore_before_new_patch(
        game_config_path,
        deliverable_root,
        failed_attempt=failed_attempt,
    )
    before = backup_config_before_patch(
        game_config_path,
        deliverable_root,
        next_attempt=next_attempt,
        artifact_root=artifact_root,
    )
    if blocked_stage_hint:
        hint_path = _backup_dir(deliverable_root) / f"blocked_stage_attempt_{failed_attempt}.txt"
        hint_path.write_text(blocked_stage_hint.strip()[:500], encoding="utf-8")
    return before, restored


def record_patch_applied(
    deliverable_root: Path,
    *,
    failed_attempt: int,
    game_config_path: Path,
    patch: GameTurboConfigPatch,
    apply_result: ConfigApplyResult,
    restored_from: str | None,
    backup_before_path: Path,
    artifact_root: Path | None,
    blocked_stage_hint: str = "",
) -> None:
    next_attempt = failed_attempt + 1
    after_path: Path | None = None
    if apply_result.changed:
        after_path = backup_config_after_patch(
            game_config_path,
            deliverable_root,
            next_attempt=next_attempt,
            artifact_root=artifact_root,
        )

    entry = ConfigRetryJournalEntry(
        failed_attempt=failed_attempt,
        next_attempt=next_attempt,
        timestamp=datetime.now(tz=UTC).isoformat(),
        game_config_path=str(game_config_path.resolve()),
        restored_from=restored_from,
        backup_before_path=str(backup_before_path.resolve()),
        backup_after_path=str(after_path.resolve()) if after_path else None,
        patch_analysis=(patch.analysis or "")[:4000],
        patch_direct_patterns=list(patch.direct_patterns),
        patch_port_rules=list(patch.port_rules),
        apply_summary=list(apply_result.summary),
        apply_changed=apply_result.changed,
        blocked_stage_hint=blocked_stage_hint,
    )
    append_journal_entry(deliverable_root, entry)
