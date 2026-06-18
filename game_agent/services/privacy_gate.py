"""路由前隐私门禁解析：区分冷启动弹窗与登录页 checkbox。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.models.privacy_gate import PrivacyGateJudgment
from game_agent.utils.ocr_util import OcrBbox
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)

_GATE_MIN_CONFIDENCE = 0.55

_DISAGREE_RE = re.compile(r"不同意|拒绝|decline|reject", re.IGNORECASE)
_MODAL_CONSENT_RE = re.compile(
    r"同意并进入|同意.*进入|Agree\s*and\s*Enter|^(同意|接受|确认|Agree|Accept)$",
    re.IGNORECASE,
)
_PRIVACY_TERMS_RE = re.compile(
    r"个人信息保护|隐私政策|用户协议|许可及服务|已阅读并同意|protect.*privacy|privacy\s*policy",
    re.IGNORECASE,
)


def ocr_has_privacy_context(ocr_merged: str) -> bool:
    return bool(_PRIVACY_TERMS_RE.search(ocr_merged or ""))


def should_invoke_privacy_gate_vlm(
    facts: LaunchFacts,
    *,
    ocr_merged: str,
    privacy_milestones_done: bool = False,
) -> bool:
    """OCR 出现隐私/协议文字且隐私里程碑未完成时，同步调 VLM 裁决分支。"""
    if facts.login_blocking or facts.sub_account_blocking:
        return False
    if facts.download_visible:
        return False
    if privacy_milestones_done:
        return False
    return ocr_has_privacy_context(ocr_merged)


def pick_consent_button_from_ocr(bboxes: list[OcrBbox]) -> tuple[int, int, str] | None:
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text or _DISAGREE_RE.search(text):
            continue
        if _MODAL_CONSENT_RE.search(text):
            candidates.append((bbox.cy, bbox))
    if not candidates:
        return None
    best = max(candidates, key=lambda item: item[0])[1]
    return best.cx, best.cy, best.text.strip()


def merge_privacy_gate_judgment(
    facts: LaunchFacts,
    judgment: PrivacyGateJudgment,
    *,
    bboxes: list[OcrBbox],
    min_confidence: float = _GATE_MIN_CONFIDENCE,
) -> LaunchFacts:
    reason = facts.classify_reason
    gate_note = (
        f"privacy_gate={judgment.gate_kind} conf={judgment.confidence:.2f} "
        f"{judgment.reason[:80]}"
    )
    reason = f"{reason}; {gate_note}" if reason else gate_note

    if judgment.is_modal(min_confidence=min_confidence):
        xy: tuple[int, int] | None = None
        if judgment.tap_x > 0 and judgment.tap_y > 0:
            xy = (judgment.tap_x, judgment.tap_y)
        elif facts.agree_button_xy is not None:
            xy = facts.agree_button_xy
        else:
            picked = pick_consent_button_from_ocr(bboxes)
            if picked is not None:
                xy = (picked[0], picked[1])
        return facts.model_copy(
            update={
                "privacy_gate_kind": "modal",
                "initial_privacy_dialog": True,
                "terms_checkbox_visible": False,
                "agree_button_xy": xy,
                "classify_reason": reason,
            },
        )

    if judgment.is_checkbox(min_confidence=min_confidence):
        return facts.model_copy(
            update={
                "privacy_gate_kind": "checkbox",
                "initial_privacy_dialog": False,
                "terms_checkbox_visible": True,
                "agree_button_xy": None,
                "classify_reason": reason,
            },
        )

    if judgment.gate_kind == "none" and judgment.confidence >= min_confidence:
        return facts.model_copy(
            update={
                "privacy_gate_kind": "none",
                "initial_privacy_dialog": False,
                "terms_checkbox_visible": False,
                "classify_reason": reason,
            },
        )

    return facts.model_copy(
        update={
            "privacy_gate_kind": "unknown",
            "initial_privacy_dialog": False,
            "terms_checkbox_visible": False,
            "classify_reason": reason,
        },
    )


def privacy_gate_vlm_unavailable(facts: LaunchFacts) -> LaunchFacts:
    """VLM 不可用时保守降级：不猜测隐私分支。"""
    return facts.model_copy(
        update={
            "privacy_gate_kind": "unknown",
            "initial_privacy_dialog": False,
            "terms_checkbox_visible": False,
            "agree_button_xy": None,
        },
    )


async def resolve_privacy_gate(
    facts: LaunchFacts,
    *,
    bboxes: list[OcrBbox],
    ocr_merged: str,
    screen_w: int,
    screen_h: int,
    screenshot_path: Path,
    llm_cfg,
    round_id: int = 0,
    privacy_milestones_done: bool = False,
) -> LaunchFacts:
    """在 plan_route 前由 VLM 统一裁决隐私门禁类型。"""
    _ = (screen_w, screen_h)

    if not should_invoke_privacy_gate_vlm(
        facts,
        ocr_merged=ocr_merged,
        privacy_milestones_done=privacy_milestones_done,
    ):
        return facts

    if llm_cfg is None:
        logger.warning("[PrivacyGate] privacy context detected but llm_multimodal unavailable")
        return privacy_gate_vlm_unavailable(facts)

    vision = VisionWorker(llm_cfg)
    judgment = await vision.judge_privacy_gate(
        screenshot_path=screenshot_path,
        ocr_summary=ocr_merged,
        round_id=round_id,
    )
    merged = merge_privacy_gate_judgment(facts, judgment, bboxes=bboxes)
    logger.info(
        "[PrivacyGate] resolved gate=%s modal=%s checkbox=%s agree=%s",
        merged.privacy_gate_kind,
        merged.initial_privacy_dialog,
        merged.terms_checkbox_visible,
        merged.agree_button_xy,
    )
    return merged
