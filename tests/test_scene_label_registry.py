"""scene_label registry 单元测试。"""

from __future__ import annotations

from pathlib import Path

from game_agent.models.scene_label import SceneLabelEntry, normalize_label_slug
from game_agent.models.scene_labels_config import SceneLabelsSection
from game_agent.services.behavior_chain import BehaviorStep
from game_agent.services.scene_label_registry import SceneLabelRegistry, compute_query_fingerprint
from game_agent.utils.ocr_util import OcrBbox


def test_normalize_label_slug() -> None:
    assert normalize_label_slug("Pre Battle Deploy!") == "pre_battle_deploy"
    assert normalize_label_slug("") == "unknown_scene"


def test_registry_bootstrap(tmp_path: Path) -> None:
    reg = SceneLabelRegistry(tmp_path, cfg=SceneLabelsSection(bootstrap_legacy_archetypes=True))
    reg.maybe_bootstrap()
    entries = reg.load_all()
    assert any(e.label_slug == "dialogue_narrative" for e in entries)


def test_registry_learn_reinforce(tmp_path: Path) -> None:
    reg = SceneLabelRegistry(tmp_path, cfg=SceneLabelsSection(bootstrap_legacy_archetypes=False))
    bboxes = [OcrBbox(text="战斗", x1=500, y1=2600, x2=700, y2=2700, cx=600, cy=2650)]
    before = "战斗\n好，开始战斗！"
    after = "loading"
    step = BehaviorStep(id="tap", action="tap_xy", x=600, y=2650)
    entry = reg.learn_from_verified_step(
        judgment=None,
        label_slug="pre_battle_deploy_tutorial_battle_cta",
        coord_strategy="pulse",
        semantic_target="战斗",
        ocr_summary=before,
        after_ocr=after,
        bboxes=bboxes,
        screen_w=1080,
        screen_h=2800,
        step=step,
        round_id=1,
        scope="pre_enter",
    )
    assert entry is not None
    assert entry.success_count == 1
    reinforced = reg.reinforce_verified(entry.label_id)
    assert reinforced is not None
    assert reinforced.success_count == 2
