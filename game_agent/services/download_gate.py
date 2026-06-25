"""资源下载 gate：OCR 优先，歧义时 VLM 裁决。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from game_agent.models.download_gate import DownloadGateJudgment
from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.utils.ocr_util import OcrBbox
from game_agent.workers.vision_worker import VisionWorker, parse_download_gate_judgment

logger = logging.getLogger(__name__)

_GATE_MIN_CONFIDENCE = 0.55

# 「更新日期」等隐私文案中的「更新」子串不应触发下载判定
_DOWNLOAD_STRONG_CONTEXT_RE = re.compile(
    r"下载|resource|download|热更|patch|资源包|资源更新",
    re.IGNORECASE,
)
_DOWNLOAD_UPDATE_VERB_RE = re.compile(
    r"正在更新|更新资源|更新中|资源更新中|热更",
    re.IGNORECASE,
)
DOWNLOAD_PROGRESS_RE = re.compile(r"\d+\s*%|%\s*\d+")
DOWNLOAD_UPDATING_RE = re.compile(r"正在更新|更新资源|资源包|下载中", re.IGNORECASE)
_SIZE_UNIT_RE = re.compile(r"\b\d+[\.,]?\d*\s*(MB|GB|KB|Mbps|Kbps|mb/s|MB/s)\b", re.IGNORECASE)
_CONTINUE_RE = re.compile(r"继续|确定|确认|Continue|OK", re.IGNORECASE)

# 屏幕顶部加速/速率条常见区域（忽略该区域的 OCR 文本参与下载判定）
_DOWNLOAD_TOP_REGION_FRACTION = 0.15


def filter_bboxes_for_download_gate(
    bboxes: list[OcrBbox],
    *,
    screen_h: int,
    top_fraction: float = _DOWNLOAD_TOP_REGION_FRACTION,
) -> list[OcrBbox]:
    if screen_h <= 0:
        return list(bboxes)
    threshold = int(screen_h * top_fraction)
    return [bbox for bbox in bboxes if int(bbox.cy) >= threshold]


def ocr_merged_for_download_gate(
    bboxes: list[OcrBbox],
    *,
    screen_h: int,
    fallback_merged: str = "",
) -> str:
    filtered = filter_bboxes_for_download_gate(bboxes, screen_h=screen_h)
    if filtered:
        return " ".join((bbox.text or "").strip() for bbox in filtered if (bbox.text or "").strip())
    return fallback_merged or ""


def ocr_has_download_context(ocr_merged: str) -> bool:
    merged = ocr_merged or ""
    if DOWNLOAD_UPDATING_RE.search(merged):
        return True
    if _DOWNLOAD_UPDATE_VERB_RE.search(merged):
        return True
    if _DOWNLOAD_STRONG_CONTEXT_RE.search(merged):
        return True
    if DOWNLOAD_PROGRESS_RE.search(merged):
        return bool(
            _DOWNLOAD_STRONG_CONTEXT_RE.search(merged)
            or DOWNLOAD_UPDATING_RE.search(merged)
            or _DOWNLOAD_UPDATE_VERB_RE.search(merged)
        )
    if _SIZE_UNIT_RE.search(merged):
        return bool(
            _DOWNLOAD_STRONG_CONTEXT_RE.search(merged)
            or DOWNLOAD_UPDATING_RE.search(merged)
            or _DOWNLOAD_UPDATE_VERB_RE.search(merged)
        )
    return False


def ocr_has_clear_download_progress(ocr_merged: str) -> bool:
    merged = ocr_merged or ""
    return bool(DOWNLOAD_PROGRESS_RE.search(merged) or DOWNLOAD_UPDATING_RE.search(merged))


def extract_download_progress_text(ocr_merged: str) -> str:
    merged = ocr_merged or ""
    m = re.search(r"\d+\s*%", merged)
    if m:
        return m.group(0).strip()
    if DOWNLOAD_UPDATING_RE.search(merged):
        return "updating"
    return ""


def pick_continue_button_from_ocr(bboxes: list[OcrBbox]) -> tuple[int, int, str] | None:
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if text and _CONTINUE_RE.search(text):
            candidates.append((bbox.cy, bbox))
    if not candidates:
        return None
    best = max(candidates, key=lambda item: item[0])[1]
    return best.cx, best.cy, best.text.strip()


def merge_download_gate_judgment(
    facts: LaunchFacts,
    judgment: DownloadGateJudgment,
    *,
    bboxes: list[OcrBbox],
    min_confidence: float = _GATE_MIN_CONFIDENCE,
) -> LaunchFacts:
    note = (
        f"download_gate={judgment.action} progress={judgment.progress_text!r} "
        f"conf={judgment.confidence:.2f} {judgment.reason[:60]}"
    )
    reason = f"{facts.classify_reason}; {note}" if facts.classify_reason else note

    if not judgment.is_download or judgment.confidence < min_confidence:
        return facts.model_copy(
            update={
                "download_visible": False,
                "download_in_progress": False,
                "download_gate_kind": "",
                "download_action": "",
                "download_continue_xy": None,
                "classify_reason": reason,
            },
        )

    if judgment.action == "done" and judgment.confidence >= min_confidence:
        return facts.model_copy(
            update={
                "download_visible": False,
                "download_gate_kind": "done",
                "download_in_progress": False,
                "download_progress_text": judgment.progress_text,
                "download_action": "done",
                "classify_reason": reason,
            },
        )

    tap_xy: tuple[int, int] | None = None
    if judgment.tap_x > 0 and judgment.tap_y > 0:
        tap_xy = (judgment.tap_x, judgment.tap_y)
    elif judgment.action == "tap_continue":
        picked = pick_continue_button_from_ocr(bboxes)
        if picked is not None:
            tap_xy = (picked[0], picked[1])

    return facts.model_copy(
        update={
            "download_visible": True,
            "download_gate_kind": "download",
            "download_in_progress": judgment.in_progress,
            "download_progress_text": judgment.progress_text,
            "download_action": judgment.action,
            "download_continue_xy": tap_xy,
            "classify_reason": reason,
        },
    )


def apply_download_ocr_to_facts(facts: LaunchFacts, *, ocr_merged: str) -> LaunchFacts:
    if not ocr_has_download_context(ocr_merged):
        return facts
    progress = extract_download_progress_text(ocr_merged)
    note = f"download_ocr progress={progress!r}" if progress else "download_ocr"
    reason = f"{facts.classify_reason}; {note}" if facts.classify_reason else note
    return facts.model_copy(
        update={
            "download_visible": True,
            "download_gate_kind": "download",
            "download_in_progress": True,
            "download_progress_text": progress,
            "download_action": "wait",
            "classify_reason": reason,
        },
    )


def should_invoke_download_gate_vlm(facts: LaunchFacts, *, ocr_merged: str) -> bool:
    if not ocr_has_download_context(ocr_merged):
        return False
    if ocr_has_clear_download_progress(ocr_merged):
        return False
    return True


async def resolve_download_gate(
    facts: LaunchFacts,
    *,
    bboxes: list[OcrBbox],
    ocr_merged: str,
    screenshot_path: Path,
    llm_cfg,
    round_id: int = 0,
    screen_h: int = 0,
) -> LaunchFacts:
    gate_ocr = ocr_merged_for_download_gate(
        bboxes,
        screen_h=screen_h,
        fallback_merged=ocr_merged,
    )
    if not ocr_has_download_context(gate_ocr):
        return facts

    if ocr_has_clear_download_progress(gate_ocr):
        return apply_download_ocr_to_facts(facts, ocr_merged=gate_ocr)

    if llm_cfg is None or not should_invoke_download_gate_vlm(facts, ocr_merged=gate_ocr):
        return apply_download_ocr_to_facts(facts, ocr_merged=gate_ocr)

    vision = VisionWorker(llm_cfg)
    judgment = await vision.judge_download_gate(
        screenshot_path=screenshot_path,
        ocr_summary=gate_ocr,
        round_id=round_id,
    )
    merged = merge_download_gate_judgment(facts, judgment, bboxes=bboxes)
    logger.info(
        "[DownloadGate] action=%s progress=%s visible=%s",
        merged.download_action,
        merged.download_progress_text,
        merged.download_visible,
    )
    return merged


def ocr_still_downloading(ocr_merged: str) -> bool:
    return ocr_has_download_context(ocr_merged)
