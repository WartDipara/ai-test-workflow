"""小号选择 gate：凭据目标 OCR 优先，坐标不足时 VLM 裁决。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from game_agent.i18n import Concept, compile_lexicon_pattern
from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.models.sub_account_gate import SubAccountGateJudgment
from game_agent.services.credentials import sub_account_target_display
from game_agent.services.login_stage_probe import probe_login_stage
from game_agent.utils.ocr_util import OcrBbox
from game_agent.workers.vision_worker import VisionWorker, parse_sub_account_gate_judgment

logger = logging.getLogger(__name__)

_GATE_MIN_CONFIDENCE = 0.55

SUB_ACCOUNT_HINT_RE = compile_lexicon_pattern(Concept.SUB_ACCOUNT)


def ocr_has_sub_account_hint(ocr_merged: str) -> bool:
    return bool(SUB_ACCOUNT_HINT_RE.search(ocr_merged or ""))


def should_invoke_sub_account_gate_vlm(
    facts: LaunchFacts,
    *,
    ocr_merged: str,
) -> bool:
    if facts.sub_account_action_xy is not None:
        return False
    if facts.sub_account_blocking:
        return True
    if ocr_has_sub_account_hint(ocr_merged) and not facts.login_blocking:
        return True
    return False


def merge_sub_account_gate_judgment(
    facts: LaunchFacts,
    judgment: SubAccountGateJudgment,
    *,
    min_confidence: float = _GATE_MIN_CONFIDENCE,
) -> LaunchFacts:
    note = (
        f"sub_account_gate conf={judgment.confidence:.2f} "
        f"label={judgment.tap_label!r} {judgment.reason[:60]}"
    )
    reason = f"{facts.classify_reason}; {note}" if facts.classify_reason else note

    if not judgment.is_sub_account or judgment.confidence < min_confidence:
        return facts.model_copy(update={"classify_reason": reason})

    tap_xy: tuple[int, int] | None = None
    if judgment.tap_x > 0 and judgment.tap_y > 0:
        tap_xy = (judgment.tap_x, judgment.tap_y)

    return facts.model_copy(
        update={
            "sub_account_blocking": True,
            "login_stage": "sub_account_select",
            "sub_account_action_xy": tap_xy,
            "sub_account_label": judgment.tap_label,
            "classify_reason": reason,
        },
    )


def apply_sub_account_ocr_probe(
    facts: LaunchFacts,
    *,
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
    sub_account_phrases: Sequence[str] | None = None,
) -> LaunchFacts:
    probe = probe_login_stage(
        bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
        sub_account_phrases=sub_account_phrases,
    )
    if probe.stage != "sub_account_select" or not probe.blocking:
        return facts
    updates: dict = {
        "sub_account_blocking": True,
        "login_stage": "sub_account_select",
    }
    if probe.action_xy is not None:
        updates["sub_account_action_xy"] = probe.action_xy
        updates["sub_account_label"] = probe.action_label
    note = f"sub_account_ocr {probe.reason}"
    reason = f"{facts.classify_reason}; {note}" if facts.classify_reason else note
    updates["classify_reason"] = reason
    return facts.model_copy(update=updates)


async def resolve_sub_account_gate(
    facts: LaunchFacts,
    *,
    bboxes: list[OcrBbox],
    ocr_merged: str,
    screen_w: int,
    screen_h: int,
    screenshot_path: Path,
    llm_cfg,
    round_id: int = 0,
    sub_account_phrases: Sequence[str] | None = None,
    sub_account_target: str = "",
    attempt_context=None,
) -> LaunchFacts:
    facts = apply_sub_account_ocr_probe(
        facts,
        bboxes=bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
        sub_account_phrases=sub_account_phrases,
    )

    if facts.sub_account_action_xy is not None:
        return facts

    if not should_invoke_sub_account_gate_vlm(facts, ocr_merged=ocr_merged):
        return facts

    if llm_cfg is None:
        logger.warning("[SubAccountGate] sub-account hints but llm_multimodal unavailable")
        return facts

    from game_agent.modules.session_invalidation import capture_session_generation, discard_if_stale

    work_gen = capture_session_generation(attempt_context)
    vision = VisionWorker(llm_cfg, attempt_context=attempt_context)
    judgment = await vision.judge_sub_account_gate(
        screenshot_path=screenshot_path,
        ocr_summary=ocr_merged,
        round_id=round_id,
        target_label=sub_account_target,
    )
    if discard_if_stale(work_gen, where="sub_account_gate", ctx=attempt_context):
        return facts
    merged = merge_sub_account_gate_judgment(facts, judgment)
    logger.info(
        "[SubAccountGate] blocking=%s tap=%s",
        merged.sub_account_blocking,
        merged.sub_account_action_xy,
    )
    return merged
