"""区服连通性多模态探针。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from game_agent.models.server_connectivity_probe import ServerConnectivityProbe
from game_agent.models.settings import LLMSection
from game_agent.services.server_error_ocr_scan import probe_from_server_error_ocr
from game_agent.utils.ocr_util import OcrBbox
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)

_VALID_SLOT = frozenset({"empty", "loading", "ready", "error", "not_visible"})
_VALID_REC = frozenset({"tap_verify", "fail_fast", "wrong_stage"})


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```json"):
        s = s[7:]
    if s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def parse_server_connectivity_probe(raw: str) -> ServerConnectivityProbe:
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        return ServerConnectivityProbe(
            reason="vision JSON parse failed",
            recommendation="tap_verify",
        )
    if not isinstance(data, dict):
        return ServerConnectivityProbe(reason="vision JSON not object", recommendation="tap_verify")

    slot = str(data.get("server_slot_status", "not_visible")).strip().lower()
    if slot not in _VALID_SLOT:
        slot = "not_visible"

    rec = str(data.get("recommendation", "")).strip().lower()
    if rec not in _VALID_REC:
        rec = _derive_recommendation(data, slot)

    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0

    return ServerConnectivityProbe(
        on_enter_game_screen=bool(data.get("on_enter_game_screen", False)),
        enter_button_visible=bool(data.get("enter_button_visible", False)),
        server_slot_status=slot,  # type: ignore[arg-type]
        server_list_likely_available=bool(data.get("server_list_likely_available", False)),
        has_network_error_ui=bool(data.get("has_network_error_ui", False)),
        confidence=max(0.0, min(1.0, conf)),
        reason=str(data.get("reason", "") or "")[:500],
        recommendation=rec,  # type: ignore[arg-type]
    )


def _derive_recommendation(data: dict, slot: str) -> str:
    if not data.get("on_enter_game_screen") and not data.get("enter_button_visible"):
        return "wrong_stage"
    if data.get("has_network_error_ui") or slot == "error":
        return "fail_fast"
    return "tap_verify"


def merge_ocr_server_error(
    probe: ServerConnectivityProbe,
    bboxes: list[OcrBbox],
) -> ServerConnectivityProbe:
    """Vision 未识别 toast 时，用全量 OCR bbox 覆盖为 fail_fast。"""
    ocr_probe = probe_from_server_error_ocr(bboxes)
    if ocr_probe is not None:
        return ocr_probe
    return probe


def format_probe_summary(probe: ServerConnectivityProbe) -> str:
    return (
        f"[ServerProbe] on_enter_game_screen={probe.on_enter_game_screen} "
        f"enter_button_visible={probe.enter_button_visible} "
        f"server_slot_status={probe.server_slot_status} "
        f"has_network_error_ui={probe.has_network_error_ui} "
        f"recommendation={probe.recommendation} conf={probe.confidence:.2f} "
        f"reason={probe.reason!r}"
    )


async def probe_server_connectivity(
    *,
    llm_cfg: LLMSection,
    screenshot_path: Path,
    ocr_summary: str,
    round_id: int = 0,
    bboxes: list[OcrBbox] | None = None,
) -> ServerConnectivityProbe:
    vision = VisionWorker(llm_cfg)
    raw = await vision.probe_server_connectivity(
        screenshot_path=screenshot_path,
        ocr_summary=ocr_summary,
        round_id=round_id,
    )
    probe = parse_server_connectivity_probe(raw)
    if bboxes:
        probe = merge_ocr_server_error(probe, bboxes)
    logger.info("%s round=%s", format_probe_summary(probe), round_id)
    return probe
