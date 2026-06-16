"""区服检查全流程：多模态探针 → OCR 锚点定位 → 点击验证。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from game_agent.models.server_connectivity_probe import ServerConnectivityProbe
from game_agent.models.settings import AppConfig
from game_agent.services.adb_service import AdbService
from game_agent.services.blocking_overlay import (
    MAX_OVERLAY_DISMISS_ATTEMPTS,
    detect_blocking_overlay,
    overlay_still_visible,
    probe_indicates_blocking_overlay,
    resolve_dismiss_target,
    verify_overlay_dismissed,
)
from game_agent.services.login_stage_probe import login_stage_gate_message
from game_agent.services.privacy_checkbox import (
    ensure_privacy_checkbox_checked_multimodal,
    message_indicates_list_panel_failed,
)
from game_agent.services.server_error_ocr_scan import (
    band_has_dash_only_slot,
    probe_from_server_error_ocr,
)
from game_agent.services.server_selector_check import (
    ServerSelectorCheckResult,
    run_server_selector_check_async,
)
from game_agent.services.server_selector_locator import (
    find_enter_game_bbox,
    locate_server_selector_target,
    server_band,
)
from game_agent.services.screen_interpreter import interpret_launch_screen
from game_agent.services.server_vision_probe import (
    format_probe_summary,
    merge_ocr_server_error,
    probe_server_connectivity,
)
from game_agent.utils.ocr_util import OcrBbox, run_ocr_frame


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
    ocr_body, bboxes = run_ocr_frame(
        shot, device_w=sw, device_h=sh, worker_key=adb.device_serial,
    )
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
    if target is None or target.source != "ocr":
        src = target.source if target else "none"
        return ServerLocatePreview(
            ok=False,
            message=(
                f"[ServerLocate] enter=({enter.cx},{enter.cy}) '{enter.text[:40]}' "
                f"no OCR server slot resolved (source={src})"
            ),
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


_SERVER_SLOT_INTERPRET_FOCUS = (
    "tap the server name bar / server slot row to OPEN the server list panel; "
    "do NOT tap Start Game or the agreement checkbox"
)


async def _resolve_server_tap_coords(
    *,
    adb: AdbService,
    artifact_root: Path,
    cfg: AppConfig,
    shot: Path,
    bboxes: list[OcrBbox],
    ocr_body: str,
    sw: int,
    sh: int,
    probe: ServerConnectivityProbe | None,
    round_id: int,
    manual_x: int = 0,
    manual_y: int = 0,
    manual_label: str = "",
) -> tuple[int, int, str, str, OcrBbox] | None:
    """OCR 定位区服栏；失败时 L2 ScreenInterpreter。返回 (x, y, label, source, enter)。"""
    if manual_x > 0 and manual_y > 0:
        enter = find_enter_game_bbox(bboxes)
        if enter is None:
            return None
        return manual_x, manual_y, manual_label, "manual", enter

    probe_hint = probe.reason if probe is not None else ""
    target, enter = locate_server_selector_target(
        bboxes,
        screen_w=sw,
        screen_h=sh,
        probe_server_name_hint=probe_hint,
    )
    if enter is None:
        return None

    if target is not None and target.source == "ocr":
        return target.cx, target.cy, target.label, "ocr", enter

    need_l2 = target is None or (
        target is not None and target.source == "unresolved"
    )
    if probe is not None and probe.server_slot_status == "ready":
        need_l2 = True

    if not need_l2 or cfg.llm_multimodal is None:
        return None

    interp = await interpret_launch_screen(
        llm_cfg=cfg.llm_multimodal,
        screenshot_path=shot,
        ocr_summary=ocr_body[:4000],
        focus=_SERVER_SLOT_INTERPRET_FOCUS,
        round_id=round_id,
    )
    tap = interp.tap_target
    if tap is None or tap.x <= 0 or tap.y <= 0:
        return None
    if tap.y >= enter.y1:
        return None
    label = tap.label or "interpreter"
    return tap.x, tap.y, label, "interpreter", enter


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


async def _try_dismiss_blocking_overlay(
    adb: AdbService,
    artifact_root: Path,
    cfg: AppConfig,
    *,
    shot: Path,
    bboxes: list[OcrBbox],
    ocr_body: str,
    sw: int,
    sh: int,
    probe: ServerConnectivityProbe | None,
    round_id: int,
) -> tuple[Path, list[OcrBbox], str, str]:
    """
    若探针/OCR 指示遮挡弹窗，解析 dismiss 坐标并点击。
    返回 (新截图, bboxes, ocr_body, dismiss_msg)。
    """
    detect = detect_blocking_overlay(
        ocr_summary=ocr_body,
        bboxes=bboxes,
        probe=probe,
    )
    if not detect.suspected and not probe_indicates_blocking_overlay(probe):
        return shot, bboxes, ocr_body, ""

    dismiss_msgs: list[str] = []
    current_shot, current_bboxes, current_ocr = shot, bboxes, ocr_body

    for attempt in range(MAX_OVERLAY_DISMISS_ATTEMPTS):
        plan = await resolve_dismiss_target(
            llm_cfg=cfg.llm_multimodal,
            screenshot_path=current_shot,
            ocr_summary=current_ocr,
            bboxes=current_bboxes,
            screen_w=sw,
            screen_h=sh,
            probe=probe,
            round_id=round_id,
        )
        if plan is None:
            dismiss_msgs.append("no dismiss plan")
            break

        tap_msg = adb.tap(plan.x, plan.y, width=sw, height=sh)
        adb.wait_seconds(0.8)
        dismiss_msgs.append(
            f"[OverlayDismiss] attempt={attempt + 1} method={plan.method} "
            f"({plan.x},{plan.y}) {plan.reason[:80]!r} | {tap_msg[:80]}"
        )

        ocr_before = current_ocr
        current_shot, current_bboxes, current_ocr, _, _ = await asyncio.to_thread(
            _capture_frame,
            adb,
            artifact_root,
            "server_overlay_dismiss",
        )
        verify = verify_overlay_dismissed(ocr_before, current_ocr)
        if verify.passed or not overlay_still_visible(current_ocr, current_bboxes):
            dismiss_msgs.append(f"[OverlayDismiss] verified: {verify.reason}")
            break
        dismiss_msgs.append(
            f"[OverlayDismiss] still visible after tap: {verify.reason}"
        )

    return current_shot, current_bboxes, current_ocr, "\n".join(dismiss_msgs) + ("\n" if dismiss_msgs else "")


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

    login_gate = login_stage_gate_message(bboxes, screen_w=sw, screen_h=sh)
    if login_gate is not None:
        return ServerSelectorCheckResult(ok=False, message=login_gate)

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

        if probe.recommendation == "dismiss_overlay" or probe.blocking_overlay:
            shot, bboxes, ocr_body, dismiss_msg = await _try_dismiss_blocking_overlay(
                adb,
                artifact_root,
                cfg,
                shot=shot,
                bboxes=bboxes,
                ocr_body=ocr_body,
                sw=sw,
                sh=sh,
                probe=probe,
                round_id=round_id,
            )
            if dismiss_msg:
                probe_msg += dismiss_msg
            if cfg.llm_multimodal is not None:
                probe = await probe_server_connectivity(
                    llm_cfg=cfg.llm_multimodal,
                    screenshot_path=shot,
                    ocr_summary=ocr_body[:4000],
                    round_id=round_id,
                    bboxes=bboxes,
                )
                probe_msg += format_probe_summary(probe) + "\n"
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
                if probe.recommendation == "dismiss_overlay" or probe.blocking_overlay:
                    return ServerSelectorCheckResult(
                        ok=False,
                        message=(
                            f"{probe_msg}[ServerCheck] WRONG_STAGE — blocking overlay "
                            "still present after dismiss attempts."
                        ),
                    )

    elif detect_blocking_overlay(ocr_summary=ocr_body, bboxes=bboxes).suspected:
        shot, bboxes, ocr_body, dismiss_msg = await _try_dismiss_blocking_overlay(
            adb,
            artifact_root,
            cfg,
            shot=shot,
            bboxes=bboxes,
            ocr_body=ocr_body,
            sw=sw,
            sh=sh,
            probe=None,
            round_id=round_id,
        )
        if dismiss_msg:
            probe_msg += dismiss_msg

    resolved = await _resolve_server_tap_coords(
        adb=adb,
        artifact_root=artifact_root,
        cfg=cfg,
        shot=shot,
        bboxes=bboxes,
        ocr_body=ocr_body,
        sw=sw,
        sh=sh,
        probe=probe,
        round_id=round_id,
        manual_x=manual_x,
        manual_y=manual_y,
        manual_label=manual_label,
    )
    if resolved is None:
        return ServerSelectorCheckResult(
            ok=False,
            message=(
                f"{probe_msg}[ServerCheck] UNRESOLVED — OCR could not locate server "
                "slot coordinates; L2 interpreter did not yield a tap above Start Game. "
                "Use report_flow_done with [E2006]."
            ),
        )
    tap_x, tap_y, label, source_note, enter = resolved

    tap_result = await run_server_selector_check_async(
        adb,
        artifact_root,
        tap_x,
        tap_y,
        enter_bbox=enter,
        label=label or source_note,
        max_taps=3,
        cfg=cfg,
        round_id=round_id,
    )
    slot_empty = _slot_empty_for_tap_upgrade(probe, bboxes, enter, sw, sh)
    return finalize_tap_check_result(
        probe_msg=probe_msg,
        probe=probe,
        tap_result=tap_result,
        slot_empty=slot_empty,
    )


def _prepend_privacy_msg(
    privacy_msg: str,
    result: ServerSelectorCheckResult,
) -> ServerSelectorCheckResult:
    if not privacy_msg:
        return result
    return ServerSelectorCheckResult(
        ok=result.ok,
        message=f"{privacy_msg}{result.message}",
        taps_used=result.taps_used,
        panel_opened=result.panel_opened,
    )


async def run_full_server_selector_check_with_privacy_precheck(
    adb: AdbService,
    artifact_root: Path,
    cfg: AppConfig,
    *,
    round_id: int = 0,
    manual_x: int = 0,
    manual_y: int = 0,
    manual_label: str = "",
    privacy_checkbox_already_tapped: bool = False,
) -> tuple[ServerSelectorCheckResult, bool]:
    """
    区服检查前尝试勾选协议 checkbox；列表未打开且尚未点过 checkbox 时补点并重试一次。
    返回 (result, privacy_checkbox_tapped_after)。
    """
    privacy_tapped = privacy_checkbox_already_tapped
    precheck_msgs: list[str] = []

    if not privacy_tapped:
        pre = await ensure_privacy_checkbox_checked_multimodal(
            adb,
            artifact_root,
            llm_cfg=cfg.llm_multimodal,
            molmopoint_cfg=cfg.molmopoint,
            prefix="privacy_cb_precheck",
            already_tapped=False,
            round_id=round_id,
        )
        precheck_msgs.append(pre.message)
        if pre.verified:
            privacy_tapped = True

    result = await run_full_server_selector_check(
        adb,
        artifact_root,
        cfg,
        round_id=round_id,
        manual_x=manual_x,
        manual_y=manual_y,
        manual_label=manual_label,
    )
    combined_precheck = "\n".join(precheck_msgs) + ("\n" if precheck_msgs else "")
    result = _prepend_privacy_msg(combined_precheck, result)

    if result.ok or privacy_tapped:
        return result, privacy_tapped

    if not message_indicates_list_panel_failed(result.message):
        return result, privacy_tapped

    retry_pre = await ensure_privacy_checkbox_checked_multimodal(
        adb,
        artifact_root,
        llm_cfg=cfg.llm_multimodal,
        molmopoint_cfg=cfg.molmopoint,
        prefix="privacy_cb_retry",
        already_tapped=False,
        round_id=round_id,
    )
    if not retry_pre.verified:
        return _prepend_privacy_msg(retry_pre.message + "\n", result), privacy_tapped

    privacy_tapped = True
    retry_result = await run_full_server_selector_check(
        adb,
        artifact_root,
        cfg,
        round_id=round_id,
        manual_x=manual_x,
        manual_y=manual_y,
        manual_label=manual_label,
    )
    retry_msg = "\n".join([retry_pre.message, "[ServerCheck] retry after privacy checkbox tap."])
    return _prepend_privacy_msg(retry_msg + "\n", retry_result), privacy_tapped
