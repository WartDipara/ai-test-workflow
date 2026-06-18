"""登录后、隐私协议 checkbox 前的服务器选择连通性检查（严格弹窗判定）。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from game_agent.services.adb_service import AdbService
from game_agent.services.server_selector_locator import (
    ENTER_POSITION_TOLERANCE_PX,
    find_enter_game_bbox,
)
from game_agent.utils.ocr_util import OcrBbox, extract_text_with_bbox

# 独立弹窗标题（整段 OCR）；勿匹配槽位提示 "Click to select Server"
_MODAL_TITLE_TEXT = re.compile(
    r"选择服务器|选择区服|服务器列表|区服列表|切换服务器|"
    r"^Select\s*Server$|^Server\s*List$",
    re.IGNORECASE,
)

# 弹窗侧栏/图例（推荐、已有角色、爆满/流畅/维护等）
_MODAL_CATEGORY_HINTS = re.compile(
    r"推荐|已有角色|最新服|我的角色|最近登录|爆满|火爆|流畅|维护|"
    r"\d+-\d+区|"
    r"recommended|maintenance|smooth|crowded",
    re.IGNORECASE,
)

_MODAL_DISMISS_HINT = re.compile(
    r"点击空白处关闭|tap.*close|click.*blank|click.*empty",
    re.IGNORECASE,
)

_MODAL_CLOSE_TEXT = re.compile(r"^(关闭|关\s*闭|×|X|Close)$", re.IGNORECASE)

_STATIC_HINT_ONLY = re.compile(
    r"^click\s*to\s*select\s*server$|^select\s*server$|^点击选择",
    re.IGNORECASE,
)

_DASH_ONLY_SLOT = re.compile(r"^-{2,}$")

_EXCLUDE_LIST_ROW = re.compile(
    r"login|password|协议|隐私|privacy|agree|copyright|publisher|版本|"
    r"sub-?account|踏入|开始游戏|进入游戏",
    re.IGNORECASE,
)

_DISMISS_HINTS = re.compile(
    r"^(关闭|关\s*闭|取消|返回|确定|OK|Close|Cancel|Back|×|X)$",
    re.IGNORECASE,
)

_EXIT_CONFIRM_NEGATIVE = re.compile(
    r"取消|返回游戏|否|暂不|继续游戏|留在此页|不退出|"
    r"cancel|stay|no|continue\s*game",
    re.IGNORECASE,
)

_EXIT_CONFIRM_POSITIVE = re.compile(
    r"退出游戏|退出|结束游戏|确认退出|是|yes|exit\s*game|quit",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PanelOcrVerdict:
    passed: bool
    evidence: str = ""
    page_navigation: bool = False
    enter_moved: bool = False


@dataclass(frozen=True, slots=True)
class ServerSelectorCheckResult:
    ok: bool
    message: str
    taps_used: int = 0
    panel_opened: bool = False


def _text_set(bboxes: list[OcrBbox]) -> set[str]:
    return {b.text.strip() for b in bboxes if b.text.strip()}


def is_page_navigation(
    before: list[OcrBbox],
    after: list[OcrBbox],
    enter_before: OcrBbox,
) -> bool:
    """整页跳转：进入按钮首次出现或位移过大。"""
    enter_after = find_enter_game_bbox(after)
    enter_was = find_enter_game_bbox(before)
    if enter_was is None and enter_after is not None:
        return True
    if enter_after is None:
        return True
    if abs(enter_after.cx - enter_before.cx) > ENTER_POSITION_TOLERANCE_PX:
        return True
    if abs(enter_after.cy - enter_before.cy) > ENTER_POSITION_TOLERANCE_PX:
        return True
    return False


def enter_still_same(enter_before: OcrBbox, after: list[OcrBbox]) -> bool:
    enter_after = find_enter_game_bbox(after)
    if enter_after is None:
        return False
    return (
        abs(enter_after.cx - enter_before.cx) <= ENTER_POSITION_TOLERANCE_PX
        and abs(enter_after.cy - enter_before.cy) <= ENTER_POSITION_TOLERANCE_PX
    )


def _has_dismiss(bboxes: list[OcrBbox]) -> bool:
    return any(_DISMISS_HINTS.search(b.text.strip()) for b in bboxes)


def _has_modal_title(bboxes: list[OcrBbox], enter: OcrBbox) -> bool:
    """弹窗标题须在进入按钮上方（独立 overlay，非底部槽位提示）。"""
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text or bbox.cy >= enter.y1:
            continue
        if _MODAL_TITLE_TEXT.search(text):
            return True
    return False


def _has_modal_close(bboxes: list[OcrBbox], enter: OcrBbox) -> bool:
    """弹窗右上角关闭钮；排除底部协议/进入区。"""
    for bbox in bboxes:
        text = bbox.text.strip()
        if not _MODAL_CLOSE_TEXT.search(text):
            continue
        if bbox.cy >= enter.y1:
            continue
        return True
    return False


def _has_modal_category(merged: str) -> bool:
    return bool(_MODAL_CATEGORY_HINTS.search(merged))


def _has_modal_dismiss_hint(bboxes: list[OcrBbox]) -> bool:
    return any(_MODAL_DISMISS_HINT.search(b.text.strip()) for b in bboxes)


def _is_meaningful_list_row(text: str) -> bool:
    """过滤 OCR 碎片、占位符；避免单字噪声被当成区服列表行。"""
    t = text.strip()
    if len(t) < 2:
        return False
    if _DASH_ONLY_SLOT.match(t):
        return False
    if _STATIC_HINT_ONLY.search(t):
        return False
    if _EXCLUDE_LIST_ROW.search(t):
        return False
    return True


def _new_list_rows_above_enter(
    before: list[OcrBbox],
    after: list[OcrBbox],
    enter: OcrBbox,
) -> list[str]:
    before_set = _text_set(before)
    added: list[str] = []
    for bbox in after:
        text = bbox.text.strip()
        if not text or text in before_set:
            continue
        if bbox.cy >= enter.y1 or bbox.cy < enter.y1 - 420:
            continue
        if not _is_meaningful_list_row(text):
            continue
        added.append(text)
    return added


def evaluate_panel_ocr(
    before: list[OcrBbox],
    after: list[OcrBbox],
    enter_before: OcrBbox,
) -> PanelOcrVerdict:
    """OCR 快判：独立区服弹窗证据；返回硬否决标记供融合层使用。"""
    if is_page_navigation(before, after, enter_before):
        return PanelOcrVerdict(
            passed=False,
            evidence="page_navigation",
            page_navigation=True,
        )
    if not enter_still_same(enter_before, after):
        return PanelOcrVerdict(
            passed=False,
            evidence="enter_cta_moved",
            enter_moved=True,
        )

    merged_after = " ".join(_text_set(after))
    new_rows = _new_list_rows_above_enter(before, after, enter_before)

    if _has_modal_title(after, enter_before):
        return PanelOcrVerdict(passed=True, evidence="modal_title")

    has_close = _has_modal_close(after, enter_before)
    if has_close and len(new_rows) >= 1:
        return PanelOcrVerdict(passed=True, evidence="close_plus_new_rows")
    if has_close and _has_modal_category(merged_after):
        return PanelOcrVerdict(passed=True, evidence="close_plus_category")

    if _has_modal_dismiss_hint(after) and _has_modal_category(merged_after):
        return PanelOcrVerdict(passed=True, evidence="dismiss_hint_plus_category")
    if _has_modal_dismiss_hint(after) and len(new_rows) >= 1:
        return PanelOcrVerdict(passed=True, evidence="dismiss_hint_plus_new_rows")

    return PanelOcrVerdict(passed=False, evidence="no_modal_evidence")


def server_list_panel_opened(
    before: list[OcrBbox],
    after: list[OcrBbox],
    enter_before: OcrBbox,
) -> bool:
    """独立区服弹窗证据：须见弹窗标题，或关闭钮+列表/侧栏结构；排除 OCR 抖动。"""
    return evaluate_panel_ocr(before, after, enter_before).passed


def find_dismiss_tap(bboxes: list[OcrBbox]) -> tuple[int, int] | None:
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = bbox.text.strip()
        if _DISMISS_HINTS.search(text):
            candidates.append((0 if text in ("关闭", "关 闭", "×", "X", "Cancel", "Close") else 1, bbox))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1].y1))
    best = candidates[0][1]
    return best.cx, best.cy


def find_exit_confirm_negative(bboxes: list[OcrBbox]) -> tuple[int, int] | None:
    negatives: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = bbox.text.strip()
        if _EXIT_CONFIRM_POSITIVE.search(text):
            continue
        if _EXIT_CONFIRM_NEGATIVE.search(text):
            negatives.append((len(text), bbox))
    if not negatives:
        return None
    negatives.sort(key=lambda item: (item[0], item[1].cy))
    best = negatives[0][1]
    return best.cx, best.cy


def has_exit_confirm_dialog(bboxes: list[OcrBbox]) -> bool:
    merged = " ".join(b.text for b in bboxes)
    if not re.search(r"退出|exit|quit", merged, re.IGNORECASE):
        return False
    return find_exit_confirm_negative(bboxes) is not None


def safe_outside_tap(width: int, height: int) -> tuple[int, int]:
    return int(width * 0.08), int(height * 0.12)


def _screencap(
    adb: AdbService,
    artifact_root: Path,
    prefix: str,
) -> tuple[Path, int, int]:
    ts = datetime.now().strftime("%H%M%S_%f")
    shot = artifact_root / f"{prefix}_{ts}.png"
    adb.screencap_png(shot)
    sw, sh = adb.touch_size()
    return shot, sw, sh


def _ocr_from_shot(shot: Path, *, device_w: int, device_h: int) -> list[OcrBbox]:
    return extract_text_with_bbox(shot, device_w=device_w, device_h=device_h)


def _capture_ocr(
    adb: AdbService,
    artifact_root: Path,
    prefix: str,
) -> tuple[Path, list[OcrBbox]]:
    shot, sw, sh = _screencap(adb, artifact_root, prefix)
    bboxes = _ocr_from_shot(shot, device_w=sw, device_h=sh)
    return shot, bboxes


def _try_close_panel(
    adb: AdbService,
    artifact_root: Path,
    width: int,
    height: int,
) -> list[str]:
    steps: list[str] = []
    _, bboxes = _capture_ocr(adb, artifact_root, "server_close")

    dismiss = find_dismiss_tap(bboxes)
    if dismiss is not None:
        dx, dy = dismiss
        adb.tap(dx, dy, width=width, height=height)
        steps.append(f"tap dismiss ({dx},{dy})")
        adb.wait_seconds(0.5)
    else:
        ox, oy = safe_outside_tap(width, height)
        adb.tap(ox, oy, width=width, height=height)
        steps.append(f"tap outside ({ox},{oy})")
        adb.wait_seconds(0.5)

    _, after_bboxes = _capture_ocr(adb, artifact_root, "server_after_dismiss")
    if has_exit_confirm_dialog(after_bboxes):
        neg = find_exit_confirm_negative(after_bboxes)
        if neg is not None:
            nx, ny = neg
            adb.tap(nx, ny, width=width, height=height)
            steps.append(f"tap exit-confirm negative ({nx},{ny})")
            adb.wait_seconds(0.4)

    return steps


def run_server_selector_check(
    adb: AdbService,
    artifact_root: Path,
    x: int,
    y: int,
    *,
    enter_bbox: OcrBbox,
    label: str = "",
    max_taps: int = 3,
) -> ServerSelectorCheckResult:
    """点击区服入口，验证同屏弹窗列表；3 次无弹窗 → FAILED。"""
    sw, sh = adb.touch_size()
    label_note = f" label={label!r}" if label else ""
    _, before_bboxes = _capture_ocr(adb, artifact_root, "server_before")

    for attempt in range(1, max_taps + 1):
        tap_msg = adb.tap(x, y, width=sw, height=sh)
        adb.wait_seconds(0.6)
        _, after_bboxes = _capture_ocr(adb, artifact_root, f"server_tap{attempt}")

        ocr_verdict = evaluate_panel_ocr(before_bboxes, after_bboxes, enter_bbox)
        if ocr_verdict.passed:
            close_steps = _try_close_panel(adb, artifact_root, sw, sh)
            return ServerSelectorCheckResult(
                ok=True,
                message=(
                    f"[ServerCheck] PASSED attempt={attempt}{label_note} "
                    f"list panel opened (same screen) source=ocr "
                    f"evidence={ocr_verdict.evidence!r}. tap={tap_msg} "
                    f"close={' | '.join(close_steps)}"
                ),
                taps_used=attempt,
                panel_opened=True,
            )
        before_bboxes = after_bboxes

    return ServerSelectorCheckResult(
        ok=False,
        message=(
            f"[ServerCheck] FAILED after {max_taps} tap(s){label_note} — "
            "server list panel did not open on same screen. "
            "Use report_flow_done with [E2006]."
        ),
        taps_used=max_taps,
        panel_opened=False,
    )


async def run_server_selector_check_async(
    adb: AdbService,
    artifact_root: Path,
    x: int,
    y: int,
    *,
    enter_bbox: OcrBbox,
    label: str = "",
    max_taps: int = 3,
    cfg: object | None = None,
    round_id: int = 0,
) -> ServerSelectorCheckResult:
    """点击区服入口；每次 tap 后 OCR 与 Vision 并行判定弹窗。"""
    from game_agent.models.settings import AppConfig
    from game_agent.services.server_panel_fusion import fuse_panel_verdict
    from game_agent.services.server_panel_verify import probe_server_panel_opened

    app_cfg = cfg if isinstance(cfg, AppConfig) else None
    executor = app_cfg.executor if app_cfg is not None else None
    fusion_enabled = True if executor is None else executor.server_panel_fusion_enabled
    min_vision_conf = 0.75 if executor is None else executor.server_panel_vision_min_conf
    llm_mm = app_cfg.llm_multimodal if app_cfg is not None else None
    use_vision = fusion_enabled and llm_mm is not None

    sw, sh = adb.touch_size()
    label_note = f" label={label!r}" if label else ""
    _, before_bboxes = await asyncio.to_thread(
        _capture_ocr, adb, artifact_root, "server_before"
    )

    for attempt in range(1, max_taps + 1):
        tap_msg = adb.tap(x, y, width=sw, height=sh)
        adb.wait_seconds(0.6)
        shot, cap_sw, cap_sh = await asyncio.to_thread(
            _screencap, adb, artifact_root, f"server_tap{attempt}"
        )

        ocr_task = asyncio.to_thread(
            _ocr_from_shot, shot, device_w=cap_sw, device_h=cap_sh
        )
        if use_vision:
            vision_task = probe_server_panel_opened(
                llm_cfg=llm_mm,
                screenshot_path=shot,
                ocr_summary="",
                round_id=round_id,
            )
            after_bboxes, vision_verdict = await asyncio.gather(ocr_task, vision_task)
        else:
            after_bboxes = await ocr_task
            vision_verdict = None

        ocr_verdict = evaluate_panel_ocr(before_bboxes, after_bboxes, enter_bbox)
        fusion = fuse_panel_verdict(
            ocr=ocr_verdict,
            vision=vision_verdict,
            min_vision_conf=min_vision_conf,
            fusion_enabled=fusion_enabled,
        )

        if fusion.passed:
            close_steps = await asyncio.to_thread(
                _try_close_panel, adb, artifact_root, sw, sh
            )
            return ServerSelectorCheckResult(
                ok=True,
                message=(
                    f"[ServerCheck] PASSED attempt={attempt}{label_note} "
                    f"list panel opened source={fusion.source} "
                    f"({fusion.message}). tap={tap_msg} "
                    f"close={' | '.join(close_steps)}"
                ),
                taps_used=attempt,
                panel_opened=True,
            )
        before_bboxes = after_bboxes

    return ServerSelectorCheckResult(
        ok=False,
        message=(
            f"[ServerCheck] FAILED after {max_taps} tap(s){label_note} — "
            "server list panel did not open on same screen. "
            "Use report_flow_done with [E2006]."
        ),
        taps_used=max_taps,
        panel_opened=False,
    )
