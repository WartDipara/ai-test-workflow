"""区服检查全流程：多模态探针 → OCR 锚点定位 → 点击验证。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from game_agent.models.server_connectivity_probe import ServerConnectivityProbe
from game_agent.models.settings import AppConfig
from game_agent.services.adb_service import AdbService
from game_agent.services.server_error_ocr_scan import (
    band_has_dash_only_slot,
    probe_from_server_error_ocr,
)
from game_agent.services.server_selector_check import (
    ServerSelectorCheckResult,
    run_server_selector_check,
)
from game_agent.services.server_selector_locator import (
    locate_server_selector_target,
    server_band,
)
from game_agent.services.server_vision_probe import (
    format_probe_summary,
    merge_ocr_server_error,
    probe_server_connectivity,
)
from game_agent.utils.ocr_util import OcrBbox, extract_text_with_bbox, extract_text_with_bounds


@dataclass(frozen=True, slots=True)
class ServerLocatePreview:
    ok: bool
    message: str
    enter_bbox: OcrBbox | None = None
    target_cx: int = 0
    target_cy: int = 0
    target_label: str = ""
    target_source: str = ""


def _capture_frame(
    adb: AdbService,
    artifact_root: Path,
    prefix: str,
) -> tuple[Path, list[OcrBbox], str, int, int]:
    ts = datetime.now().strftime("%H%M%S_%f")
    shot = artifact_root / f"{prefix}_{ts}.png"
    adb.screencap_png(shot)
    sw, sh = adb.touch_size()
    bboxes = extract_text_with_bbox(shot, device_w=sw, device_h=sh)
    ocr_body = extract_text_with_bounds(shot, device_w=sw, device_h=sh)
    return shot, bboxes, ocr_body, sw, sh


def preview_server_selector_locate(
    adb: AdbService,
    artifact_root: Path,
) -> ServerLocatePreview:
    _, bboxes, _, sw, sh = _capture_frame(adb, artifact_root, "server_locate")
    target, enter = locate_server_selector_target(bboxes, screen_w=sw, screen_h=sh)
    if enter is None:
        return ServerLocatePreview(
            ok=False,
            message="[ServerLocate] WRONG_STAGE: enter-game button not found in OCR",
        )
    if target is None:
        return ServerLocatePreview(
            ok=False,
            message="[ServerLocate] could not derive target above enter button",
            enter_bbox=enter,
        )
    return ServerLocatePreview(
        ok=True,
        message=(
            f"[ServerLocate] enter=({enter.cx},{enter.cy}) '{enter.text[:40]}' "
            f"target=({target.cx},{target.cy}) source={target.source} "
            f"label={target.label!r}"
        ),
        enter_bbox=enter,
        target_cx=target.cx,
        target_cy=target.cy,
        target_label=target.label,
        target_source=target.source,
    )


def _fail_fast_e2006(probe_msg: str, reason: str) -> ServerSelectorCheckResult:
    return ServerSelectorCheckResult(
        ok=False,
        message=(
            f"{probe_msg}[ServerCheck] FAILED [E2006] — network/server "
            f"error UI ({reason})"
        ),
    )


def _ocr_indicates_empty_slot(
    bboxes: list[OcrBbox],
    enter: OcrBbox,
    screen_w: int,
    screen_h: int,
) -> bool:
    band = server_band(enter, screen_w, screen_h)
    return band_has_dash_only_slot(
        bboxes,
        band_y1=band.y1,
        band_y2=band.y2,
        band_x1=band.x1,
        band_x2=band.x2,
    )


def _slot_empty_for_tap_upgrade(
    probe: ServerConnectivityProbe | None,
    bboxes: list[OcrBbox],
    enter: OcrBbox,
    screen_w: int,
    screen_h: int,
) -> bool:
    if probe is not None and probe.server_slot_status == "empty":
        return True
    return _ocr_indicates_empty_slot(bboxes, enter, screen_w, screen_h)


def message_indicates_e2006(message: str) -> bool:
    return "[E2006]" in (message or "")


def finalize_tap_check_result(
    *,
    probe_msg: str,
    probe: ServerConnectivityProbe | None,
    tap_result: ServerSelectorCheckResult,
    slot_empty: bool,
) -> ServerSelectorCheckResult:
    """tap 验证后：empty 槽且列表未打开 → 升级为 E2006。"""
    if tap_result.ok:
        return ServerSelectorCheckResult(
            ok=True,
            message=f"{probe_msg}{tap_result.message}",
            taps_used=tap_result.taps_used,
            panel_opened=tap_result.panel_opened,
        )
    if slot_empty:
        return ServerSelectorCheckResult(
            ok=False,
            message=(
                f"{probe_msg}[ServerCheck] FAILED [E2006] — empty server slot and "
                "list panel did not open after tap verification."
            ),
            taps_used=tap_result.taps_used,
            panel_opened=False,
        )
    return ServerSelectorCheckResult(
        ok=False,
        message=f"{probe_msg}{tap_result.message}",
        taps_used=tap_result.taps_used,
        panel_opened=tap_result.panel_opened,
    )


async def run_full_server_selector_check(
    adb: AdbService,
    artifact_root: Path,
    cfg: AppConfig,
    *,
    round_id: int = 0,
    manual_x: int = 0,
    manual_y: int = 0,
    manual_label: str = "",
) -> ServerSelectorCheckResult:
    shot, bboxes, ocr_body, sw, sh = await asyncio.to_thread(
        _capture_frame,
        adb,
        artifact_root,
        "server_probe",
    )

    probe: ServerConnectivityProbe | None = None
    probe_msg = ""

    ocr_error_probe = probe_from_server_error_ocr(bboxes)
    if ocr_error_probe is not None:
        probe_msg = format_probe_summary(ocr_error_probe) + "\n"
        return _fail_fast_e2006(probe_msg, ocr_error_probe.reason)

    if cfg.llm_multimodal is not None:
        probe = await probe_server_connectivity(
            llm_cfg=cfg.llm_multimodal,
            screenshot_path=shot,
            ocr_summary=ocr_body[:4000],
            round_id=round_id,
            bboxes=bboxes,
        )
        probe_msg = format_probe_summary(probe) + "\n"
        if probe.recommendation == "wrong_stage":
            return ServerSelectorCheckResult(
                ok=False,
                message=(
                    f"{probe_msg}[ServerCheck] WRONG_STAGE — not on enter-game screen. "
                    "Complete login/sub-account until enter-game CTA visible."
                ),
            )
        if probe.recommendation == "fail_fast":
            return _fail_fast_e2006(probe_msg, probe.reason)

    target, enter = locate_server_selector_target(bboxes, screen_w=sw, screen_h=sh)
    if enter is None:
        return ServerSelectorCheckResult(
            ok=False,
            message=f"{probe_msg}[ServerCheck] WRONG_STAGE — enter-game button not found",
        )

    if manual_x > 0 and manual_y > 0:
        tap_x, tap_y, label = manual_x, manual_y, manual_label
        source_note = "manual"
    elif target is not None:
        tap_x, tap_y, label = target.cx, target.cy, target.label
        source_note = target.source
    else:
        return ServerSelectorCheckResult(
            ok=False,
            message=f"{probe_msg}[ServerCheck] WRONG_STAGE — cannot locate server slot",
        )

    tap_result = await asyncio.to_thread(
        run_server_selector_check,
        adb,
        artifact_root,
        tap_x,
        tap_y,
        enter_bbox=enter,
        label=label or source_note,
        max_taps=3,
    )
    slot_empty = _slot_empty_for_tap_upgrade(probe, bboxes, enter, sw, sh)
    return finalize_tap_check_result(
        probe_msg=probe_msg,
        probe=probe,
        tap_result=tap_result,
        slot_empty=slot_empty,
    )
