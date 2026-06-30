"""scene_gate 开放 label 解析测试。"""

from __future__ import annotations

from game_agent.workers.vision_worker import parse_scene_gate_judgment


def test_parse_scene_gate_open_label() -> None:
    raw = """{
      "label_slug": "pre_battle_deploy_tutorial_battle_cta",
      "label_display": "战前布阵",
      "confidence": 0.9,
      "coord_strategy": "pulse",
      "semantic_target": "战斗",
      "description": "battle prep",
      "reason": "finger on battle button"
    }"""
    j = parse_scene_gate_judgment(raw)
    assert j.normalized_slug() == "pre_battle_deploy_tutorial_battle_cta"
    assert j.normalized_coord_strategy() == "pulse"
    assert j.semantic_target == "战斗"
    assert j.normalized_scene_id() == "tutorial"


def test_parse_scene_gate_legacy_compat() -> None:
    raw = """{
      "scene_id": "dialogue",
      "confidence": 0.85,
      "action": "tap_dialogue",
      "description": "speech bubble"
    }"""
    j = parse_scene_gate_judgment(raw)
    assert j.normalized_slug() == "dialogue"
    assert j.normalized_coord_strategy() == "ocr"
