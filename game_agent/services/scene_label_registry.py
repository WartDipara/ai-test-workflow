"""统一场景标记注册表：retrieve / learn / trace。"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.scene_label import (
    CoordStrategy,
    SceneLabelEntry,
    SceneLabelJudgment,
    SceneLabelMatch,
    SceneLabelScope,
    SceneLabelTraceEvent,
    normalize_label_slug,
)
from game_agent.models.scene_labels_config import SceneLabelsSection
from game_agent.models.scene_memory import SceneMemoryAction, SceneMemoryEntry, SceneMemoryMatch
from game_agent.services.behavior_chain import BehaviorStep
from game_agent.services.scene_memory_playbook import (
    detect_scene_archetype,
    fingerprint_similarity,
    memory_action_from_step,
    verify_memory_progress,
)
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)

MEMORIES_JSONL = "memories.jsonl"
INDEX_JSON = "index.json"
TRACE_JSONL = "trace.jsonl"
SUMMARY_MD = "scene_label_summary.md"
SUMMARY_JSON = "scene_label_summary.json"

_MIN_SUCCESS_FOR_FAST = 1


def compute_query_fingerprint(
    *,
    ocr_summary: str,
    bboxes: list[OcrBbox],
    screen_h: int,
    label_slug: str = "",
) -> str:
    prefix = normalize_label_slug(label_slug) if label_slug else "struct"
    if screen_h > 0 and bboxes:
        bottom = sorted(
            (b.text or "").strip()[:40]
            for b in bboxes
            if b.cy >= int(screen_h * 0.45)
        )
        body = "|".join(bottom[:10])
    else:
        body = (ocr_summary or "")[:200]
    return f"{prefix}|{body}"[:320]


def new_label_id(label_slug: str, fingerprint: str) -> str:
    raw = f"{label_slug}|{fingerprint}|{datetime.now(tz=UTC).isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _coord_to_resolver(strategy: CoordStrategy) -> str:
    if strategy == "dim_region":
        return "dim_region"
    if strategy == "pulse":
        return "fixed_xy"
    return "screen_ratio"


def _bootstrap_entries() -> list[SceneLabelEntry]:
    return [
        SceneLabelEntry(
            label_id="seed_dialogue_narrative",
            label_slug="dialogue_narrative",
            label_display="剧情对话，点台词气泡推进",
            coord_strategy="ocr",
            semantic_target="",
            structural_fingerprint="dialogue_narr|struct",
            execution_policy=SceneMemoryAction(
                resolver="screen_ratio",
                x_ratio=0.5,
                y_ratio=0.82,
                intent="dialogue_narrative",
            ),
            scope="both",
            success_count=1,
            confidence=0.7,
            source="bootstrap",
            notes="legacy archetype seed",
        ),
        SceneLabelEntry(
            label_id="seed_dialogue_blank",
            label_slug="dialogue_blank_continue",
            label_display="点击空白继续",
            coord_strategy="dim_region",
            structural_fingerprint="dialogue_blank|dim_overlay",
            execution_policy=SceneMemoryAction(
                resolver="dim_region",
                intent="dialogue_blank_continue",
            ),
            scope="both",
            success_count=1,
            confidence=0.7,
            source="bootstrap",
            notes="legacy archetype seed",
        ),
    ]


@dataclass(frozen=True, slots=True)
class SceneLabelRegistry:
    artifact_root: Path
    cfg: SceneLabelsSection | None = None

    @property
    def label_dir(self) -> Path:
        return self.artifact_root / "scene_labels"

    @property
    def memories_path(self) -> Path:
        return self.label_dir / MEMORIES_JSONL

    @property
    def trace_path(self) -> Path:
        return self.label_dir / TRACE_JSONL

    def _settings(self) -> SceneLabelsSection:
        return self.cfg or SceneLabelsSection()

    def ensure_dir(self) -> None:
        self.label_dir.mkdir(parents=True, exist_ok=True)

    def maybe_bootstrap(self) -> None:
        cfg = self._settings()
        if not cfg.bootstrap_legacy_archetypes:
            return
        if self.memories_path.is_file() and self.memories_path.stat().st_size > 0:
            return
        self.ensure_dir()
        for entry in _bootstrap_entries():
            with self.memories_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry.model_dump(), ensure_ascii=False) + "\n")
        self._refresh_index()
        logger.info("[SceneLabel] bootstrapped %d seed entries", len(_bootstrap_entries()))

    def load_all(self) -> list[SceneLabelEntry]:
        self.maybe_bootstrap()
        path = self.memories_path
        if not path.is_file():
            return []
        entries: list[SceneLabelEntry] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(SceneLabelEntry.model_validate(json.loads(line)))
            except Exception:
                continue
        return entries

    def append(self, entry: SceneLabelEntry) -> None:
        if entry.success_count < 1:
            logger.warning("[SceneLabel] refused append: success_count < 1")
            return
        self.ensure_dir()
        with self.memories_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.model_dump(), ensure_ascii=False) + "\n")
        self._refresh_index()
        logger.info(
            "[SceneLabel] saved id=%s slug=%s conf=%.2f success=%d",
            entry.label_id,
            entry.label_slug,
            entry.confidence,
            entry.success_count,
        )

    def _rewrite_all(self, entries: list[SceneLabelEntry]) -> None:
        self.ensure_dir()
        lines = [json.dumps(e.model_dump(), ensure_ascii=False) for e in entries]
        self.memories_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        self._refresh_index()

    def reinforce_verified(self, label_id: str) -> SceneLabelEntry | None:
        entries = self.load_all()
        target: SceneLabelEntry | None = None
        updated: list[SceneLabelEntry] = []
        for entry in entries:
            if entry.label_id == label_id:
                entry = entry.model_copy(
                    update={
                        "success_count": entry.success_count + 1,
                        "confidence": min(1.0, entry.confidence + 0.08),
                    }
                )
                target = entry
            updated.append(entry)
        if target is None:
            return None
        self._rewrite_all(updated)
        return target

    def revoke_label(self, label_id: str) -> bool:
        entries = self.load_all()
        kept = [e for e in entries if e.label_id != label_id]
        if len(kept) == len(entries):
            return False
        self._rewrite_all(kept)
        logger.info("[SceneLabel] revoked id=%s", label_id)
        return True

    def demote_label(self, label_id: str) -> SceneLabelEntry | None:
        entries = self.load_all()
        target: SceneLabelEntry | None = None
        updated: list[SceneLabelEntry] = []
        for entry in entries:
            if entry.label_id == label_id:
                entry = entry.model_copy(
                    update={"confidence": max(0.1, entry.confidence - 0.15)}
                )
                target = entry
            updated.append(entry)
        if target is None:
            return None
        self._rewrite_all(updated)
        return target

    def list_known_labels_for_prompt(self, *, scope: SceneLabelScope | None = None, limit: int = 20) -> list[SceneLabelEntry]:
        cfg = self._settings()
        cap = limit if limit > 0 else cfg.max_known_labels_in_prompt
        entries = [
            e
            for e in self.load_all()
            if e.success_count >= _MIN_SUCCESS_FOR_FAST
            and (scope is None or e.scope in (scope, "both"))
        ]
        entries.sort(key=lambda e: (e.success_count, e.confidence), reverse=True)
        return entries[:cap]

    def retrieve(
        self,
        *,
        ocr_summary: str,
        bboxes: list[OcrBbox],
        screen_h: int,
        scope: SceneLabelScope,
        vlm_hint_label_id: str = "",
    ) -> SceneLabelMatch | None:
        if not self._settings().enabled:
            return None
        cfg = self._settings()
        query_fp = compute_query_fingerprint(
            ocr_summary=ocr_summary,
            bboxes=bboxes,
            screen_h=screen_h,
        )
        best: SceneLabelMatch | None = None
        for entry in self.load_all():
            if entry.scope not in (scope, "both"):
                continue
            if entry.success_count < _MIN_SUCCESS_FOR_FAST:
                continue
            if entry.confidence < cfg.min_learn_confidence:
                continue
            sim = fingerprint_similarity(query_fp, entry.structural_fingerprint)
            if vlm_hint_label_id and entry.label_id == vlm_hint_label_id:
                sim = min(1.0, sim + 0.15)
            for alias in entry.aliases:
                if alias and alias in (ocr_summary or ""):
                    sim = min(1.0, sim + 0.05)
            if sim < cfg.min_retrieve_similarity:
                continue
            score = sim * 0.6 + entry.confidence * 0.4
            candidate = SceneLabelMatch(entry=entry, similarity=score)
            if best is None or candidate.similarity > best.similarity:
                best = candidate
        if best is not None:
            logger.info(
                "[SceneLabel] retrieve hit id=%s slug=%s score=%.2f",
                best.entry.label_id,
                best.entry.label_slug,
                best.similarity,
            )
        return best

    def apply_judgment_to_state(
        self,
        state: dict,
        judgment: SceneLabelJudgment,
        *,
        matched: SceneLabelMatch | None = None,
    ) -> None:
        slug = judgment.normalized_slug()
        state["scene_label_slug"] = slug
        state["scene_label_display"] = (judgment.label_display or judgment.description)[:300]
        state["scene_label_coord_strategy"] = (
            matched.entry.coord_strategy if matched else judgment.normalized_coord_strategy()
        )
        state["scene_label_semantic_target"] = (
            matched.entry.semantic_target if matched else (judgment.semantic_target or "")[:80]
        )
        state["scene_label_id"] = matched.entry.label_id if matched else ""
        state["scene_label_fast_path"] = matched is not None
        state["scene_gate_scene_id"] = judgment.legacy_scene_id()
        state["scene_gate_confidence"] = judgment.confidence
        state["scene_gate_description"] = judgment.description[:300]
        state["scene_gate_action"] = judgment.normalized_coord_strategy()
        state["scene_gate_use_dim_region_tap"] = judgment.use_dim_region_tap
        state["scene_gate_dim_region_hint"] = str(judgment.dim_region_hint or "")[:300]

    def log_trace(self, event: SceneLabelTraceEvent) -> None:
        self.ensure_dir()
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")

    def learn_from_verified_step(
        self,
        *,
        judgment: SceneLabelJudgment | None,
        label_slug: str,
        coord_strategy: CoordStrategy,
        semantic_target: str,
        ocr_summary: str,
        after_ocr: str,
        bboxes: list[OcrBbox],
        screen_w: int,
        screen_h: int,
        step: BehaviorStep,
        round_id: int,
        screenshot_ref: str = "",
        screen_analysis: InGameScreenAnalysis | None = None,
        scope: SceneLabelScope = "both",
        source: str = "slow_path",
    ) -> SceneLabelEntry | None:
        cfg = self._settings()
        if not cfg.enabled:
            return None
        if step.action not in ("tap_xy",) or step.x <= 0 or step.y <= 0:
            return None
        slug = normalize_label_slug(label_slug or (judgment.normalized_slug() if judgment else ""))
        if slug == "unknown_scene":
            archetype = detect_scene_archetype(
                ocr_summary,
                bboxes=bboxes,
                screen_analysis=screen_analysis,
                screen_h=screen_h,
            )
            if archetype == "unknown":
                return None
            slug = archetype
        if not verify_memory_progress(
            "dialogue_narrative" if "dialogue" in slug else "unknown",
            before_ocr=ocr_summary,
            after_ocr=after_ocr,
            screen_analysis=screen_analysis,
        ):
            if (ocr_summary or "").strip() == (after_ocr or "").strip():
                logger.info("[SceneLabel] skip learn: no verified progress slug=%s", slug)
                return None
        fp = compute_query_fingerprint(
            label_slug=slug,
            ocr_summary=ocr_summary,
            bboxes=bboxes,
            screen_h=screen_h,
        )
        strategy = coord_strategy
        if judgment is not None and strategy == "none":
            strategy = judgment.normalized_coord_strategy()
        if strategy == "none":
            strategy = "ocr"
        target = (semantic_target or "").strip()
        if not target and judgment is not None:
            target = (judgment.semantic_target or "").strip()
        mem_action = memory_action_from_step(
            step,
            screen_w=screen_w,
            screen_h=screen_h,
            archetype="dialogue_narrative",  # type: ignore[arg-type]
        )
        mem_action = mem_action.model_copy(
            update={
                "resolver": _coord_to_resolver(strategy),  # type: ignore[arg-type]
                "intent": f"scene_label:{slug}",
            }
        )
        existing = next(
            (
                e
                for e in self.load_all()
                if e.label_slug == slug
                and fingerprint_similarity(fp, e.structural_fingerprint) >= 0.55
            ),
            None,
        )
        if existing is not None:
            return self.reinforce_verified(existing.label_id)
        entry = SceneLabelEntry(
            label_id=new_label_id(slug, fp),
            label_slug=slug,
            label_display=(judgment.label_display if judgment else slug)[:200],
            coord_strategy=strategy,
            semantic_target=target[:80],
            structural_fingerprint=fp,
            ocr_skeleton=ocr_summary[:240],
            execution_policy=mem_action,
            scope=scope,
            success_count=1,
            confidence=max(cfg.min_learn_confidence, judgment.confidence if judgment else 0.62),
            learned_at_round=round_id,
            source=source,
            screenshot_ref=screenshot_ref,
            notes=f"verified from {step.id}",
        )
        self.append(entry)
        return entry

    def _refresh_index(self) -> None:
        entries = self.load_all()
        by_slug: dict[str, int] = {}
        for e in entries:
            by_slug[e.label_slug] = by_slug.get(e.label_slug, 0) + 1
        index = {
            "updated_at": datetime.now(tz=UTC).isoformat(),
            "total": len(entries),
            "by_slug": by_slug,
            "artifact_root": str(self.artifact_root.resolve()),
        }
        (self.label_dir / INDEX_JSON).write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_summary_files(self) -> None:
        if not self.label_dir.is_dir():
            return
        entries = self.load_all()
        summary = {
            "artifact_root": str(self.artifact_root.resolve()),
            "total_labels": len(entries),
            "entries": [e.model_dump() for e in entries],
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }
        (self.label_dir / SUMMARY_JSON).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # --- scene_memory 兼容层 ---

    def retrieve_as_memory(
        self,
        *,
        ocr_summary: str,
        bboxes: list[OcrBbox],
        screen_h: int,
        screen_analysis: InGameScreenAnalysis | None = None,
    ) -> SceneMemoryMatch | None:
        match = self.retrieve(
            ocr_summary=ocr_summary,
            bboxes=bboxes,
            screen_h=screen_h,
            scope="in_game",
        )
        if match is None:
            return None
        entry = match.entry
        mem_entry = SceneMemoryEntry(
            memory_id=entry.label_id,
            archetype=_slug_to_archetype(entry.label_slug),
            structural_fingerprint=entry.structural_fingerprint,
            ocr_skeleton=entry.ocr_skeleton,
            primary_action=entry.execution_policy,
            success_count=entry.success_count,
            confidence=entry.confidence,
            learned_at_round=entry.learned_at_round,
            source=entry.source,
            screenshot_ref=entry.screenshot_ref,
            notes=entry.notes,
        )
        return SceneMemoryMatch(
            entry=mem_entry,
            similarity=match.similarity,
            archetype=_slug_to_archetype(entry.label_slug),
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
        label_slug: str = "",
        coord_strategy: CoordStrategy = "none",
        semantic_target: str = "",
    ) -> SceneMemoryEntry | None:
        label = self.learn_from_verified_step(
            judgment=None,
            label_slug=label_slug,
            coord_strategy=coord_strategy,
            semantic_target=semantic_target,
            ocr_summary=ocr_summary,
            after_ocr=after_ocr,
            bboxes=bboxes,
            screen_w=screen_w,
            screen_h=screen_h,
            step=step,
            round_id=round_id,
            screenshot_ref=screenshot_ref,
            screen_analysis=screen_analysis,
            scope="in_game",
            source=source,
        )
        if label is None:
            return None
        return SceneMemoryEntry(
            memory_id=label.label_id,
            archetype=_slug_to_archetype(label.label_slug),
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


def _slug_to_archetype(slug: str) -> Any:
    from game_agent.models.scene_memory import SceneArchetype

    s = slug.lower()
    if "technique" in s:
        return "technique_selection"
    if "blank" in s:
        return "dialogue_blank_continue"
    if "dialogue" in s or "narrative" in s:
        return "dialogue_narrative"
    return "dialogue_narrative"
