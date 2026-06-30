"""场景记忆 RAG 存储与检索单测。"""

from __future__ import annotations

import json
from pathlib import Path

from game_agent.models.scene_memory import SceneMemoryAction, SceneMemoryEntry
from game_agent.services.behavior_chain import BehaviorStep
from game_agent.services.scene_memory_playbook import (
    build_chain_from_memory,
    compute_structural_fingerprint,
    detect_scene_archetype,
    fingerprint_similarity,
    resolve_center_card_column,
    verify_memory_progress,
)
from game_agent.services.scene_memory_store import SceneMemoryStore, export_scene_memory_to_deliverable
from game_agent.utils.ocr_util import OcrBbox

_TECHNIQUE_OCR = (
    "Technique Selection StormExtension Sandstorm Duration+100% "
    "Ice Spike DMG Inc Damage+ Duration+"
)


def test_detect_technique_archetype() -> None:
    assert detect_scene_archetype(_TECHNIQUE_OCR) == "technique_selection"


def test_fingerprint_similarity_ignores_numbers() -> None:
    a = "technique|StormExtension|Duration+|Damage+"
    b = "technique|Fireball|Duration+|Damage+"
    assert fingerprint_similarity(a, b) >= 0.5


def test_store_learn_and_retrieve(tmp_path: Path) -> None:
    store = SceneMemoryStore(tmp_path)
    bboxes = [
        OcrBbox(text="Technique", x1=480, y1=690, x2=600, y2=720, cx=540, cy=705),
        OcrBbox(text="Selection", x1=480, y1=740, x2=600, y2=770, cx=540, cy=755),
        OcrBbox(text="Sandstorm", x1=480, y1=930, x2=600, y2=960, cx=540, cy=945),
        OcrBbox(text="Duration+", x1=480, y1=1280, x2=600, y2=1310, cx=540, cy=1295),
    ]
    fp = compute_structural_fingerprint(
        "technique_selection",
        ocr_summary=_TECHNIQUE_OCR,
        bboxes=bboxes,
        screen_h=2400,
    )
    entry = SceneMemoryEntry(
        memory_id="abc123",
        archetype="technique_selection",
        structural_fingerprint=fp,
        primary_action=SceneMemoryAction(
            resolver="screen_ratio",
            x_ratio=0.5,
            y_ratio=0.4,
            x=540,
            y=960,
        ),
        success_count=2,
        confidence=0.7,
    )
    store.append(entry)
    match = store.retrieve(
        ocr_summary=_TECHNIQUE_OCR,
        bboxes=bboxes,
        screen_h=2400,
    )
    assert match is not None
    assert match.entry.memory_id == "abc123"


def test_learn_from_step(tmp_path: Path) -> None:
    store = SceneMemoryStore(tmp_path)
    bboxes = [
        OcrBbox(text="Technique", x1=480, y1=690, x2=600, y2=720, cx=540, cy=705),
        OcrBbox(text="Selection", x1=480, y1=740, x2=600, y2=770, cx=540, cy=755),
        OcrBbox(text="Sandstorm", x1=480, y1=930, x2=600, y2=960, cx=540, cy=945),
    ]
    step = BehaviorStep(
        id="vlm_fusion_tap",
        action="tap_xy",
        x=540,
        y=954,
        intent="tap center card",
    )
    learned = store.learn_from_successful_step(
        ocr_summary=_TECHNIQUE_OCR,
        after_ocr="Above Cangfeng Canyon Level 10 combat hud",
        bboxes=bboxes,
        screen_w=1080,
        screen_h=2400,
        step=step,
        round_id=5,
    )
    assert learned is not None
    assert learned.archetype == "technique_selection"
    assert store.memories_path.is_file()


def test_learn_skips_when_scene_did_not_progress(tmp_path: Path) -> None:
    store = SceneMemoryStore(tmp_path)
    bboxes = [
        OcrBbox(text="Technique", x1=480, y1=690, x2=600, y2=720, cx=540, cy=705),
        OcrBbox(text="Selection", x1=480, y1=740, x2=600, y2=770, cx=540, cy=755),
    ]
    step = BehaviorStep(id="tap", action="tap_xy", x=540, y=954)
    learned = store.learn_from_successful_step(
        ocr_summary=_TECHNIQUE_OCR,
        after_ocr=_TECHNIQUE_OCR,
        bboxes=bboxes,
        screen_w=1080,
        screen_h=2400,
        step=step,
        round_id=1,
    )
    assert learned is None
    assert not store.memories_path.is_file()


def test_revoke_memory(tmp_path: Path) -> None:
    store = SceneMemoryStore(tmp_path)
    store.append(
        SceneMemoryEntry(
            memory_id="rm1",
            archetype="technique_selection",
            primary_action=SceneMemoryAction(x=540, y=954),
            success_count=1,
        )
    )
    assert store.revoke_memory("rm1")
    assert store.load_all() == []


def test_build_chain_from_memory() -> None:
    entry = SceneMemoryEntry(
        memory_id="x",
        archetype="technique_selection",
        primary_action=SceneMemoryAction(
            resolver="screen_ratio",
            x_ratio=0.5,
            y_ratio=0.4,
        ),
    )
    chain = build_chain_from_memory(
        entry,
        bboxes=[],
        screen_w=1080,
        screen_h=2400,
    )
    assert chain is not None
    assert chain.steps[0].x == 540
    assert chain.steps[0].y == 960


def test_verify_technique_gone() -> None:
    before = _TECHNIQUE_OCR
    after = "Above Cangfeng Canyon Level 10 combat hud"
    assert verify_memory_progress("technique_selection", before_ocr=before, after_ocr=after)


def test_export_to_deliverable(tmp_path: Path) -> None:
    art = tmp_path / "retry_1"
    art.mkdir()
    store = SceneMemoryStore(art)
    store.append(
        SceneMemoryEntry(
            memory_id="m1",
            archetype="technique_selection",
            primary_action=SceneMemoryAction(x=540, y=954, x_ratio=0.5, y_ratio=0.4),
            success_count=1,
            confidence=0.6,
        )
    )
    deliverable = tmp_path / "out"
    deliverable.mkdir()
    dst = export_scene_memory_to_deliverable(deliverable, [(1, art)])
    assert dst is not None
    merged = deliverable / "scene_memory" / "memories_merged.jsonl"
    assert merged.is_file()
    rows = [json.loads(line) for line in merged.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["memory_id"] == "m1"
    assert rows[0]["_attempt"] == 1


def test_resolve_center_card_column() -> None:
    bboxes = [
        OcrBbox(text="Ice", x1=100, y1=900, x2=200, y2=930, cx=150, cy=915),
        OcrBbox(text="Sandstorm", x1=480, y1=900, x2=600, y2=930, cx=540, cy=915),
        OcrBbox(text="Storm", x1=880, y1=900, x2=980, y2=930, cx=930, cy=915),
    ]
    xy = resolve_center_card_column(bboxes, screen_w=1080, screen_h=2400)
    assert xy == (540, 915)
