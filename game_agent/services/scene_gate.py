"""场景 gate：VLM 开放场景标记 + 注册表复用。"""

from __future__ import annotations

import logging
from pathlib import Path

from game_agent.models.launch_graph_state import LaunchFacts, LaunchGraphState
from game_agent.models.scene import SceneClassification
from game_agent.models.scene_gate import SceneGateJudgment
from game_agent.models.scene_label import SceneLabelTraceEvent
from game_agent.services.scene_classifier import compute_scene_fingerprint
from game_agent.services.scene_label_registry import SceneLabelRegistry
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
    if state.get("session_agent_active"):
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
    """VLM 置信足够时用开放 label 映射的 legacy scene_id 覆盖规则分类。"""
    sid = judgment.normalized_scene_id()
    slug = judgment.normalized_slug()
    note = (
        f"scene_gate={slug} conf={judgment.confidence:.2f} "
        f"strategy={judgment.normalized_coord_strategy()} {judgment.description[:60]}"
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
    skip_vlm: bool = False,
    artifact_root: Path | None = None,
    scene_labels_cfg=None,
    attempt_context=None,
) -> tuple[SceneClassification, SceneGateJudgment | None]:
    registry: SceneLabelRegistry | None = None
    if artifact_root is not None:
        registry = SceneLabelRegistry(artifact_root, cfg=scene_labels_cfg)
        if registry._settings().enabled:
            match = registry.retrieve(
                ocr_summary=ocr_merged,
                bboxes=bboxes,
                screen_h=screen_h,
                scope="pre_enter",
            )
            if match is not None:
                judgment = SceneGateJudgment(
                    label_slug=match.entry.label_slug,
                    label_display=match.entry.label_display,
                    coord_strategy=match.entry.coord_strategy,
                    semantic_target=match.entry.semantic_target,
                    match_prior_label_id=match.entry.label_id,
                    confidence=match.entry.confidence,
                    description=match.entry.label_display,
                    reason="registry_fast_path",
                )
                registry.apply_judgment_to_state(state, judgment.to_scene_label_judgment(), matched=match)
                state["scene_label_fast_path"] = True
                state["last_scene_label_judgment"] = judgment.model_dump()
                merged, _ = merge_scene_gate_judgment(
                    rule_cls,
                    judgment,
                    bboxes=bboxes,
                    ocr_summary=ocr_merged,
                    screen_h=screen_h,
                )
                registry.log_trace(
                    SceneLabelTraceEvent(
                        round_id=round_id,
                        node="classify",
                        vlm_label_slug=match.entry.label_slug,
                        matched_label_id=match.entry.label_id,
                        is_new_label=False,
                        coord_strategy=match.entry.coord_strategy,
                        semantic_target=match.entry.semantic_target,
                        screenshot_ref=str(screenshot_path),
                        ocr_head=ocr_merged[:120],
                    )
                )
                logger.info(
                    "[SceneGate] registry hit slug=%s id=%s strategy=%s",
                    match.entry.label_slug,
                    match.entry.label_id,
                    match.entry.coord_strategy,
                )
                return merged, judgment

    if skip_vlm:
        return rule_cls, None
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

    known_rows: list[dict] = []
    if registry is not None:
        known_rows = [
            {
                "label_id": e.label_id,
                "label_slug": e.label_slug,
                "coord_strategy": e.coord_strategy,
                "semantic_target": e.semantic_target,
                "label_display": e.label_display,
            }
            for e in registry.list_known_labels_for_prompt(scope="pre_enter")
        ]

    vision = VisionWorker(llm_cfg, attempt_context=attempt_context)
    from game_agent.modules.session_invalidation import capture_session_generation, discard_if_stale

    work_gen = capture_session_generation(attempt_context)
    judgment = await vision.judge_scene_gate(
        screenshot_path=screenshot_path,
        ocr_summary=ocr_merged,
        rule_scene_id=rule_cls.scene_id,
        rule_confidence=rule_cls.confidence,
        active_strategy=str(state.get("active_scene_strategy") or state.get("scene_label_slug") or ""),
        round_id=round_id,
        known_labels=known_rows,
    )
    if discard_if_stale(work_gen, where="resolve_scene_gate", ctx=attempt_context):
        logger.warning("[SceneGate] discard stale VLM judgment — keep OCR rule")
        return rule_cls, None
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
    state["scene_gate_use_dim_region_tap"] = bool(judgment.use_dim_region_tap)
    state["scene_gate_dim_region_hint"] = str(judgment.dim_region_hint or "")[:300]

    label_j = judgment.to_scene_label_judgment()
    if registry is not None:
        registry.apply_judgment_to_state(state, label_j, matched=None)
        registry.log_trace(
            SceneLabelTraceEvent(
                round_id=round_id,
                node="classify",
                vlm_label_slug=label_j.normalized_slug(),
                vlm_label_display=label_j.label_display,
                matched_label_id=judgment.match_prior_label_id,
                is_new_label=True,
                coord_strategy=label_j.normalized_coord_strategy(),
                semantic_target=label_j.semantic_target,
                screenshot_ref=str(screenshot_path),
                ocr_head=ocr_merged[:120],
            )
        )
    else:
        state["scene_label_slug"] = label_j.normalized_slug()
        state["scene_label_display"] = label_j.label_display
        state["scene_label_coord_strategy"] = label_j.normalized_coord_strategy()
        state["scene_label_semantic_target"] = label_j.semantic_target
    state["last_scene_label_judgment"] = judgment.model_dump()
    state["scene_label_fast_path"] = False

    logger.info(
        "[SceneGate] rule=%s@%.2f → vlm slug=%s@%.2f strategy=%s desc=%s",
        rule_cls.scene_id,
        rule_cls.confidence,
        judgment.normalized_slug(),
        merged.confidence,
        judgment.normalized_coord_strategy(),
        judgment.description[:80],
    )
    return merged, judgment


def scene_id_from_scene_gate(state: LaunchGraphState, *, fallback: str) -> str:
    """VLM / label 定性优先于 OCR 规则 scene_id。"""
    slug = str(state.get("scene_label_slug") or "").strip()
    if slug and slug != "unknown_scene":
        gate_conf = float(state.get("scene_gate_confidence") or 0)
        if gate_conf >= _VLM_OVERRIDE_MIN_CONFIDENCE:
            return str(state.get("scene_gate_scene_id") or fallback)
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
    VLM 仅决定 wait；tap 类动作返回 None，由 plan_from_scene_label / plan_scene_action 定位坐标。
    """
    from game_agent.models.scene import SceneActionPlan

    scene_id = scene_id_from_scene_gate(state, fallback=scene_id)
    strategy = str(state.get("scene_label_coord_strategy") or "").strip().lower()
    action = str(state.get("scene_gate_action") or "").strip().lower()

    if (strategy == "wait" or action == "wait") and scene_id == "loading":
        return SceneActionPlan(
            action="wait",
            wait_s=2.5,
            reason="scene_gate:vlm_wait",
            mode="wait_observe",
        )

    return None
