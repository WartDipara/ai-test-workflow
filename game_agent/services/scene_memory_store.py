"""artifacts 内场景记忆 JSONL 存储与 RAG 检索。"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.scene_memory import SceneArchetype, SceneMemoryEntry, SceneMemoryMatch
from game_agent.services.behavior_chain import BehaviorStep
from game_agent.services.scene_label_registry import SceneLabelRegistry
from game_agent.services.scene_memory_playbook import (
    compute_structural_fingerprint,
    detect_scene_archetype,
    fingerprint_similarity,
    memory_action_from_step,
    new_memory_id,
    verify_memory_progress,
)
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)

MEMORIES_JSONL = "memories.jsonl"
INDEX_JSON = "index.json"
SUMMARY_MD = "scene_memory_summary.md"
SUMMARY_JSON = "scene_memory_summary.json"

def _label_slug_to_archetype(slug: str) -> SceneArchetype:
    s = (slug or "").lower()
    if "technique" in s:
        return "technique_selection"
    if "blank" in s:
        return "dialogue_blank_continue"
    return "dialogue_narrative"


@dataclass(frozen=True, slots=True)
class SceneMemoryStore:
    artifact_root: Path

    @property
    def memory_dir(self) -> Path:
        return self.artifact_root / "scene_labels"

    @property
    def _registry(self) -> SceneLabelRegistry:
        return SceneLabelRegistry(self.artifact_root)

    @property
    def memories_path(self) -> Path:
        return self.memory_dir / MEMORIES_JSONL

    def ensure_dir(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[SceneMemoryEntry]:
        entries: list[SceneMemoryEntry] = []
        for label in self._registry.load_all():
            entries.append(
                SceneMemoryEntry(
                    memory_id=label.label_id,
                    archetype=_label_slug_to_archetype(label.label_slug),
                    structural_fingerprint=label.structural_fingerprint,
                    ocr_skeleton=label.ocr_skeleton,
                    primary_action=label.execution_policy,
                    success_count=label.success_count,
                    failure_count=label.failure_count,
                    confidence=label.confidence,
                    learned_at_round=label.learned_at_round,
                    source=label.source,
                    screenshot_ref=label.screenshot_ref,
                    notes=label.notes,
                )
            )
        return entries

    def append(self, entry: SceneMemoryEntry) -> None:
        """兼容旧 API；新写入请走 SceneLabelRegistry。"""
        from game_agent.models.scene_label import SceneLabelEntry

        label = SceneLabelEntry(
            label_id=entry.memory_id,
            label_slug=str(entry.archetype),
            structural_fingerprint=entry.structural_fingerprint,
            ocr_skeleton=entry.ocr_skeleton,
            execution_policy=entry.primary_action,
            success_count=entry.success_count,
            confidence=entry.confidence,
            learned_at_round=entry.learned_at_round,
            source=entry.source,
            screenshot_ref=entry.screenshot_ref,
            notes=entry.notes,
        )
        self._registry.append(label)

    def reinforce_verified(self, memory_id: str) -> SceneMemoryEntry | None:
        label = self._registry.reinforce_verified(memory_id)
        if label is None:
            return None
        return SceneMemoryEntry(
            memory_id=label.label_id,
            archetype=_label_slug_to_archetype(label.label_slug),
            structural_fingerprint=label.structural_fingerprint,
            ocr_skeleton=label.ocr_skeleton,
            primary_action=label.execution_policy,
            success_count=label.success_count,
            confidence=label.confidence,
            learned_at_round=label.learned_at_round,
            source=label.source,
            screenshot_ref=label.screenshot_ref,
            notes=label.notes,
        )

    def revoke_memory(self, memory_id: str) -> bool:
        return self._registry.revoke_label(memory_id)

    def demote_memory(self, memory_id: str) -> SceneMemoryEntry | None:
        label = self._registry.demote_label(memory_id)
        if label is None:
            return None
        return SceneMemoryEntry(
            memory_id=label.label_id,
            archetype=_label_slug_to_archetype(label.label_slug),
            structural_fingerprint=label.structural_fingerprint,
            ocr_skeleton=label.ocr_skeleton,
            primary_action=label.execution_policy,
            success_count=label.success_count,
            confidence=label.confidence,
            learned_at_round=label.learned_at_round,
            source=label.source,
            screenshot_ref=label.screenshot_ref,
            notes=label.notes,
        )

    def _rewrite_all(self, entries: list[SceneMemoryEntry]) -> None:
        from game_agent.models.scene_label import SceneLabelEntry

        labels = [
            SceneLabelEntry(
                label_id=e.memory_id,
                label_slug=str(e.archetype),
                structural_fingerprint=e.structural_fingerprint,
                ocr_skeleton=e.ocr_skeleton,
                execution_policy=e.primary_action,
                success_count=e.success_count,
                failure_count=e.failure_count,
                confidence=e.confidence,
                learned_at_round=e.learned_at_round,
                source=e.source,
                screenshot_ref=e.screenshot_ref,
                notes=e.notes,
            )
            for e in entries
        ]
        self._registry._rewrite_all(labels)

    def retrieve(
        self,
        *,
        ocr_summary: str,
        bboxes: list[OcrBbox],
        screen_h: int,
        screen_analysis: InGameScreenAnalysis | None = None,
    ) -> SceneMemoryMatch | None:
        _ = screen_analysis
        return self._registry.retrieve_as_memory(
            ocr_summary=ocr_summary,
            bboxes=bboxes,
            screen_h=screen_h,
            screen_analysis=screen_analysis,
        )

    def learn_from_successful_step(
        self,
        *,
        ocr_summary: str,
        after_ocr: str,
        bboxes: list[OcrBbox],
        screen_w: int,
        screen_h: int,
        step: BehaviorStep,
        round_id: int,
        screenshot_ref: str = "",
        screen_analysis: InGameScreenAnalysis | None = None,
        source: str = "slow_path",
    ) -> SceneMemoryEntry | None:
        return self._registry.learn_from_successful_step(
            ocr_summary=ocr_summary,
            after_ocr=after_ocr,
            bboxes=bboxes,
            screen_w=screen_w,
            screen_h=screen_h,
            step=step,
            round_id=round_id,
            screenshot_ref=screenshot_ref,
            screen_analysis=screen_analysis,
            source=source,
        )

    def _refresh_index(self) -> None:
        self._registry._refresh_index()

    def build_summary(self) -> dict[str, Any]:
        entries = self.load_all()
        return {
            "artifact_root": str(self.artifact_root.resolve()),
            "total_memories": len(entries),
            "entries": [e.model_dump() for e in entries],
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }

    def write_summary_files(self) -> None:
        self._registry.write_summary_files()


def export_scene_memory_to_deliverable(
    deliverable_root: Path,
    attempt_records: list[tuple[int, Path]],
) -> Path | None:
    """任务结束时将各 attempt 的 scene_memory 合并写入 run_outputs。"""
    dst = deliverable_root / "scene_memory"
    dst.mkdir(parents=True, exist_ok=True)
    merged_entries: list[dict[str, Any]] = []
    copied = False
    for retry_no, artifact_root in attempt_records:
        src_dir = artifact_root / "scene_labels"
        if not src_dir.is_dir():
            src_dir = artifact_root / "scene_memory"
        if not src_dir.is_dir():
            continue
        copied = True
        attempt_dst = dst / f"attempt_{retry_no}"
        attempt_dst.mkdir(parents=True, exist_ok=True)
        for name in (MEMORIES_JSONL, INDEX_JSON, SUMMARY_JSON, SUMMARY_MD):
            src = src_dir / name
            if src.is_file():
                shutil.copy2(src, attempt_dst / name)
        mem_path = src_dir / MEMORIES_JSONL
        if mem_path.is_file():
            for line in mem_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    row["_attempt"] = retry_no
                    merged_entries.append(row)
                except json.JSONDecodeError:
                    continue
    if not copied:
        return None
    merged_path = dst / "memories_merged.jsonl"
    with merged_path.open("w", encoding="utf-8") as f:
        for row in merged_entries:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "total_merged": len(merged_entries),
        "attempts": len({r.get("_attempt") for r in merged_entries}),
        "by_archetype": {},
        "generated_at": datetime.now(tz=UTC).isoformat(),
    }
    for row in merged_entries:
        arch = str(row.get("archetype") or "unknown")
        summary["by_archetype"][arch] = summary["by_archetype"].get(arch, 0) + 1
    (dst / SUMMARY_JSON).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_lines = [
        "# Scene Memory (task export)",
        "",
        f"- merged entries: {summary['total_merged']}",
        f"- attempts with memory: {summary['attempts']}",
        "",
        "## By archetype",
        "",
    ]
    for arch, count in sorted(summary["by_archetype"].items()):
        md_lines.append(f"- {arch}: {count}")
    md_lines.extend(["", f"See `{merged_path.name}` and `attempt_*/` for details.", ""])
    (dst / SUMMARY_MD).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    logger.info(
        "[SceneMemory] exported %d entries to %s",
        len(merged_entries),
        dst,
    )
    return dst
