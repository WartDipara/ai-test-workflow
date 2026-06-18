"""场景 gate：登录后由 VLM 描述/定性画面，优先于纯 OCR 规则。"""

from __future__ import annotations

import logging
from pathlib import Path

from game_agent.models.launch_graph_state import LaunchFacts, LaunchGraphState
from game_agent.models.scene import SceneClassification
from game_agent.models.scene_gate import SceneGateJudgment
from game_agent.services.scene_classifier import compute_scene_fingerprint
from game_agent.utils.ocr_util import OcrBbox
from game_agent.workers.vision_worker import VisionWorker, parse_scene_gate_judgment

logger = logging.getLogger(__name__)

_GATE_MIN_CONFIDENCE = 0.55
_VLM_OVERRIDE_MIN_CONFIDENCE = 0.50


def should_invoke_scene_gate_vlm(
    state: LaunchGraphState,
    facts: LaunchFacts,
    *,
    rule_classification: SceneClassification,
) -> bool:
    """登录完成、无静态业务阻塞时，由 VLM 对场景定性。"""
    from game_agent.graphs.static_priority import has_pending_static_work

    if not state.get("login_done"):
        return False
    if state.get("in_game_confirmed") or state.get("in_game_entry_passed"):
        return False
    if has_pending_static_work(state, facts):
        return False
    if facts.login_blocking or facts.sub_account_blocking or facts.initial_privacy_dialog:
        return False
    if facts.download_visible:
        return False
    return True


def merge_scene_gate_judgment(
    rule_cls: SceneClassification,
    judgment: SceneGateJudgment,
    *,
    bboxes: list[OcrBbox],
    ocr_summary: str,
    screen_h: int,
    min_confidence: float = _GATE_MIN_CONFIDENCE,
) -> tuple[SceneClassification, SceneGateJudgment]:
    """VLM 置信足够时覆盖 OCR 规则分类。"""
    sid = judgment.normalized_scene_id()
    note = (
        f"scene_gate={sid} conf={judgment.confidence:.2f} "
        f"action={judgment.action} {judgment.description[:60]}"
    )

    if judgment.confidence < min_confidence and rule_cls.confidence >= min_confidence:
        return rule_cls, judgment

    use_vlm = judgment.confidence >= _VLM_OVERRIDE_MIN_CONFIDENCE and sid != "unknown"
    if not use_vlm and rule_cls.scene_id != "unknown" and rule_cls.confidence >= min_confidence:
        return rule_cls, judgment

    if not use_vlm:
        evidence = rule_cls.evidence
        if note:
            evidence = f"{evidence}; {note}" if evidence else note
        return rule_cls.model_copy(update={"evidence": evidence[:300]}), judgment

    conf = max(judgment.confidence, rule_cls.confidence if sid == rule_cls.scene_id else judgment.confidence)
    fp = compute_scene_fingerprint(
        sid,
        ocr_summary=ocr_summary,
        bboxes=bboxes,
        screen_h=screen_h,
    )
    evidence = note
    if rule_cls.scene_id != sid and rule_cls.confidence >= 0.45:
        evidence = f"{note}; ocr_rule={rule_cls.scene_id}@{rule_cls.confidence:.2f}"

    return SceneClassification(
        scene_id=sid,  # type: ignore[arg-type]
        confidence=conf,
        evidence=evidence[:300],
        fingerprint=fp,
        source="vlm",
    ), judgment


async def resolve_scene_gate(
    state: LaunchGraphState,
    rule_cls: SceneClassification,
    *,
    facts: LaunchFacts,
    bboxes: list[OcrBbox],
    ocr_merged: str,
    screen_h: int,
    screenshot_path: Path,
    llm_cfg,
    round_id: int = 0,
    screenshot_hash: str = "",
) -> tuple[SceneClassification, SceneGateJudgment | None]:
    if not should_invoke_scene_gate_vlm(state, facts, rule_classification=rule_cls):
        return rule_cls, None

    if screenshot_hash and screenshot_hash == state.get("scene_gate_screenshot_hash"):
        cached_sid = str(state.get("scene_gate_scene_id") or "")
        cached_conf = float(state.get("scene_gate_confidence") or 0)
        if cached_sid:
            cached = SceneClassification(
                scene_id=cached_sid,  # type: ignore[arg-type]
                confidence=cached_conf,
                evidence=str(state.get("scene_gate_description") or "")[:300],
                fingerprint=compute_scene_fingerprint(
                    cached_sid,
                    ocr_summary=ocr_merged,
                    bboxes=bboxes,
                    screen_h=screen_h,
                ),
                source="vlm_cached",
            )
            return cached, None

    if llm_cfg is None:
        logger.warning("[SceneGate] login_done but llm_multimodal unavailable — keep OCR rule")
        return rule_cls, None

    vision = VisionWorker(llm_cfg)
    judgment = await vision.judge_scene_gate(
        screenshot_path=screenshot_path,
        ocr_summary=ocr_merged,
        rule_scene_id=rule_cls.scene_id,
        rule_confidence=rule_cls.confidence,
        active_strategy=str(state.get("active_scene_strategy") or ""),
        round_id=round_id,
    )
    merged, _ = merge_scene_gate_judgment(
        rule_cls,
        judgment,
        bboxes=bboxes,
        ocr_summary=ocr_merged,
        screen_h=screen_h,
    )

    state["scene_gate_screenshot_hash"] = screenshot_hash
    state["scene_gate_scene_id"] = merged.scene_id
    state["scene_gate_confidence"] = merged.confidence
    state["scene_gate_description"] = judgment.description[:300]
    state["scene_gate_action"] = judgment.normalized_action()

    logger.info(
        "[SceneGate] rule=%s@%.2f → vlm=%s@%.2f action=%s desc=%s",
        rule_cls.scene_id,
        rule_cls.confidence,
        merged.scene_id,
        merged.confidence,
        judgment.action,
        judgment.description[:80],
    )
    return merged, judgment


def scene_id_from_scene_gate(state: LaunchGraphState, *, fallback: str) -> str:
    """VLM 定性优先于 OCR 规则 scene_id。"""
    gate_scene = str(state.get("scene_gate_scene_id") or "")
    gate_conf = float(state.get("scene_gate_confidence") or 0)
    if gate_scene in ("dialogue", "tutorial", "loading") and gate_conf >= _VLM_OVERRIDE_MIN_CONFIDENCE:
        return gate_scene
    return fallback


def plan_from_scene_gate(
    state: LaunchGraphState,
    *,
    scene_id: str,
) -> "SceneActionPlan | None":
    """
    VLM 仅决定 wait；tap 类动作返回 None，由 plan_scene_action + OCR 定位坐标。
    """
    from game_agent.models.scene import SceneActionPlan

    scene_id = scene_id_from_scene_gate(state, fallback=scene_id)
    action = str(state.get("scene_gate_action") or "").strip().lower()

    if action == "wait" and scene_id == "loading":
        return SceneActionPlan(
            action="wait",
            wait_s=2.5,
            reason="scene_gate:vlm_wait",
            mode="wait_observe",
        )

    return None
