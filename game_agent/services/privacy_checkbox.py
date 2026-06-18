"""进入游戏前协议 checkbox：OCR 左推定位 + 多模态状态判定 + ROI 差分辅助。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from game_agent.models.privacy_checkbox_judgment import PrivacyCheckboxJudgment
from game_agent.services.adb_service import AdbService
from game_agent.services.checkbox_locator import (
    CheckboxLocateResult,
    find_privacy_terms_anchor,
    locate_checkbox_tap,
    locate_privacy_checkbox,
)
from game_agent.utils.ocr_util import OcrBbox, extract_text_with_bbox, extract_text_with_bounds
from game_agent.workers.vision_worker import VisionWorker

if TYPE_CHECKING:
    from game_agent.models.settings import LLMSection, MolmopointSection

logger = logging.getLogger(__name__)

# checkbox ROI 平均灰度差超过此阈值视为「已发生变化」（出现勾/高亮/填充）
_ROI_CHANGE_THRESHOLD = 6.0
_MAX_TAP_STEPS = 3
_VISION_CHECKED_CONFIDENCE = 0.55


@dataclass(frozen=True, slots=True)
class PrivacyCheckboxEnsureResult:
    """ensure_privacy_checkbox_checked 结果。"""

    action: str  # tapped | skipped | already_checked | failed
    message: str
    tapped: bool = False
    verified: bool = False
    roi_diff: float = 0.0
    vision_state: str = ""
    vision_confidence: float = 0.0
    locate: CheckboxLocateResult | None = None
    screenshot: Path | None = None
    after_screenshot: Path | None = None
    debug_marked_image: Path | None = None


def screen_has_privacy_terms(
    bboxes: list[OcrBbox],
    screen_w: int,
    screen_h: int,
) -> bool:
    """当前 OCR 是否可见协议文字行（可推导 checkbox）。"""
    return locate_privacy_checkbox(bboxes, screen_w, screen_h, step=0) is not None


def message_indicates_list_panel_failed(message: str) -> bool:
    """区服 tap 验证失败：同屏列表弹窗未打开。"""
    text = (message or "").lower()
    return (
        "list panel did not open" in text
        or "empty server slot and list panel did not open" in text
    )


def checkbox_roi_box(
    located: CheckboxLocateResult,
    screen_w: int,
    screen_h: int,
) -> tuple[int, int, int, int]:
    """以推导点击点为中心的正方形 ROI（device 逻辑像素）。"""
    line_h = max(1, located.line_y2 - located.line_y1)
    half = max(20, located.half_char_px * 2, line_h)
    x1 = max(0, located.cx - half)
    x2 = min(screen_w, located.cx + half)
    y1 = max(0, located.cy - half)
    y2 = min(screen_h, located.cy + half)
    if x2 <= x1 or y2 <= y1:
        return 0, 0, min(screen_w, half * 2), min(screen_h, half * 2)
    return x1, y1, x2, y2


def roi_mean_abs_diff(
    before_path: Path,
    after_path: Path,
    box: tuple[int, int, int, int],
) -> float:
    """裁剪 ROI 后计算灰度平均绝对差；用于判断 checkbox 是否发生视觉变化。"""
    from PIL import Image

    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1:
        return 0.0
    with Image.open(before_path) as before_im, Image.open(after_path) as after_im:
        bw, bh = before_im.size
        aw, ah = after_im.size
        crop = (
            max(0, min(x1, bw - 1)),
            max(0, min(y1, bh - 1)),
            max(1, min(x2, bw)),
            max(1, min(y2, bh)),
        )
        if crop[2] <= crop[0] or crop[3] <= crop[1]:
            return 0.0
        before_gray = before_im.convert("L").crop(crop)
        after_gray = after_im.convert("L").crop(
            (
                max(0, min(x1, aw - 1)),
                max(0, min(y1, ah - 1)),
                max(1, min(x2, aw)),
                max(1, min(y2, ah)),
            )
        )
        if before_gray.size != after_gray.size:
            return 0.0
        from PIL import ImageChops, ImageStat

        diff_im = ImageChops.difference(before_gray, after_gray)
        return float(ImageStat.Stat(diff_im).mean[0])


def mark_checkbox_tap_on_image(
    image_path: Path,
    output_path: Path,
    *,
    cx: int,
    cy: int,
    roi_box: tuple[int, int, int, int] | None = None,
    radius: int = 12,
) -> Path:
    """在截图上标记 tap 红点与可选 ROI 框，供离线调试验证坐标。"""
    from PIL import Image, ImageDraw

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as im:
        marked = im.convert("RGB").copy()
    draw = ImageDraw.Draw(marked)
    if roi_box is not None:
        x1, y1, x2, y2 = roi_box
        draw.rectangle([x1, y1, x2, y2], outline="lime", width=3)
    r = max(4, radius)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="red", outline="white", width=2)
    draw.line([cx - r * 2, cy, cx + r * 2, cy], fill="yellow", width=2)
    draw.line([cx, cy - r * 2, cx, cy + r * 2], fill="yellow", width=2)
    marked.save(output_path)
    return output_path


def _vision_trusts_checked(state: str, confidence: float, *, min_conf: float) -> bool:
    return state == "checked" and confidence >= min_conf


def _locate_for_tap_attempt(
    bboxes,
    *,
    before_shot: Path,
    sw: int,
    sh: int,
    molmopoint_cfg: MolmopointSection | None,
    step: int,
    try_molmopoint: bool,
) -> CheckboxLocateResult | None:
    """单次点击尝试的定位：可选 MolmoPoint（仅首轮）或指定 step 的 OCR 左推。"""
    return locate_checkbox_tap(
        bboxes,
        sw,
        sh,
        image_path=before_shot,
        molmopoint_cfg=molmopoint_cfg,
        step=step,
        try_molmopoint=try_molmopoint,
    )


async def ensure_privacy_checkbox_checked_multimodal(
    adb: AdbService,
    artifact_root: Path,
    *,
    llm_cfg: LLMSection | None,
    molmopoint_cfg: MolmopointSection | None = None,
    step: int | None = None,
    prefix: str = "privacy_cb",
    already_tapped: bool = False,
    round_id: int = 0,
    max_steps: int = 1,
    change_threshold: float = _ROI_CHANGE_THRESHOLD,
    vision_confidence: float = _VISION_CHECKED_CONFIDENCE,
) -> PrivacyCheckboxEnsureResult:
    """
    多模态 ensure：点击前判定是否已勾选；未勾选则 tap 一次后用 before/after 多模态 + ROI 辅助验证。
    llm_cfg 为空时退回同步 ROI-only ensure。
    """
    if llm_cfg is None:
        return ensure_privacy_checkbox_checked(
            adb,
            artifact_root,
            step=step,
            prefix=prefix,
            already_tapped=already_tapped,
            max_steps=max_steps if step is None else 0,
            change_threshold=change_threshold,
            molmopoint_cfg=molmopoint_cfg,
        )

    if already_tapped:
        return PrivacyCheckboxEnsureResult(
            action="skipped",
            message="[PrivacyCheckbox] SKIPPED — already verified this session.",
            tapped=False,
            verified=True,
        )

    ts = datetime.now().strftime("%H%M%S_%f")
    before_shot = artifact_root / f"{prefix}_before_{ts}.png"
    adb.screencap_png(before_shot)
    sw, sh = adb.touch_size()
    bboxes = extract_text_with_bbox(before_shot, device_w=sw, device_h=sh)
    if not bboxes:
        return PrivacyCheckboxEnsureResult(
            action="skipped",
            message="[PrivacyCheckbox] SKIPPED — OCR found no text.",
            screenshot=before_shot,
        )

    try_step = 0 if step is None else step
    located = _locate_for_tap_attempt(
        bboxes,
        before_shot=before_shot,
        sw=sw,
        sh=sh,
        molmopoint_cfg=molmopoint_cfg,
        step=try_step,
        try_molmopoint=(try_step == 0),
    )
    if located is None:
        return PrivacyCheckboxEnsureResult(
            action="skipped",
            message="[PrivacyCheckbox] SKIPPED — no matching terms/agree line in OCR.",
            screenshot=before_shot,
        )

    box = checkbox_roi_box(located, sw, sh)
    ocr_summary = extract_text_with_bounds(before_shot, device_w=sw, device_h=sh)
    vision = VisionWorker(llm_cfg)

    before_judgment = await vision.judge_privacy_checkbox_state(
        screenshot_path=before_shot,
        ocr_summary=ocr_summary,
        candidate_cx=located.cx,
        candidate_cy=located.cy,
        roi_box=box,
        round_id=round_id,
    )
    if _vision_trusts_checked(
        before_judgment.state,
        before_judgment.confidence,
        min_conf=vision_confidence,
    ):
        debug_path = artifact_root / f"{prefix}_before_marked_{ts}.png"
        mark_checkbox_tap_on_image(
            before_shot,
            debug_path,
            cx=located.cx,
            cy=located.cy,
            roi_box=box,
        )
        return PrivacyCheckboxEnsureResult(
            action="already_checked",
            message=(
                f"[PrivacyCheckbox] ALREADY CHECKED (vision before tap) "
                f"state={before_judgment.state} conf={before_judgment.confidence:.2f} "
                f"{before_judgment.reason} "
                f"{located.format_message(prefix='[PrivacyCheckbox]')} "
                f"marked={debug_path.resolve()}"
            ),
            tapped=False,
            verified=True,
            vision_state=before_judgment.state,
            vision_confidence=before_judgment.confidence,
            locate=located,
            screenshot=before_shot,
            debug_marked_image=debug_path,
        )

    if before_judgment.suggests_consent_button(min_confidence=vision_confidence):
        return PrivacyCheckboxEnsureResult(
            action="failed",
            message=(
                "[PrivacyCheckbox] MISROUTED — vision sees consent-button modal, not checkbox; "
                "privacy_gate should route to handle_initial_privacy_dialog "
                f"state={before_judgment.state} conf={before_judgment.confidence:.2f} "
                f"{before_judgment.reason}"
            ),
            screenshot=before_shot,
            vision_state=before_judgment.state,
            vision_confidence=before_judgment.confidence,
            locate=located,
        )

    tap_msg = adb.tap(located.cx, located.cy, width=sw, height=sh)
    adb.wait_seconds(0.4)
    after_ts = datetime.now().strftime("%H%M%S_%f")
    after_shot = artifact_root / f"{prefix}_after_s{try_step}_{after_ts}.png"
    adb.screencap_png(after_shot)
    diff = roi_mean_abs_diff(before_shot, after_shot, box)

    after_judgment = await vision.judge_privacy_checkbox_state(
        screenshot_path=after_shot,
        ocr_summary=ocr_summary,
        candidate_cx=located.cx,
        candidate_cy=located.cy,
        roi_box=box,
        before_screenshot_path=before_shot,
        round_id=round_id,
    )

    debug_path = artifact_root / f"{prefix}_after_marked_{after_ts}.png"
    mark_checkbox_tap_on_image(
        after_shot,
        debug_path,
        cx=located.cx,
        cy=located.cy,
        roi_box=box,
    )

    vision_ok = _vision_trusts_checked(
        after_judgment.state,
        after_judgment.confidence,
        min_conf=vision_confidence,
    )
    roi_ok = diff >= change_threshold

    if vision_ok or roi_ok:
        via = "vision+roi" if vision_ok and roi_ok else ("vision" if vision_ok else "roi_diff")
        return PrivacyCheckboxEnsureResult(
            action="tapped",
            message=(
                f"[PrivacyCheckbox] VERIFIED ({via}) {tap_msg} "
                f"vision={after_judgment.state} conf={after_judgment.confidence:.2f} "
                f"roi_diff={diff:.1f} threshold={change_threshold} "
                f"{after_judgment.reason} "
                f"{located.format_message(prefix='[PrivacyCheckbox]')} "
                f"before={before_shot.resolve()} after={after_shot.resolve()} "
                f"marked={debug_path.resolve()}"
            ),
            tapped=True,
            verified=True,
            roi_diff=diff,
            vision_state=after_judgment.state,
            vision_confidence=after_judgment.confidence,
            locate=located,
            screenshot=before_shot,
            after_screenshot=after_shot,
            debug_marked_image=debug_path,
        )

    # 点击后无视觉变化：可能本就已勾选（before 误判为 unchecked）
    if (
        diff < change_threshold
        and before_judgment.state in ("unchecked", "uncertain")
        and after_judgment.state != "unchecked"
        and after_judgment.confidence >= vision_confidence * 0.8
    ):
        return PrivacyCheckboxEnsureResult(
            action="already_checked",
            message=(
                f"[PrivacyCheckbox] ALREADY CHECKED (no ROI change, vision infers selected) "
                f"before={before_judgment.state} after={after_judgment.state} "
                f"roi_diff={diff:.1f} {after_judgment.reason}"
            ),
            tapped=True,
            verified=True,
            roi_diff=diff,
            vision_state=after_judgment.state,
            vision_confidence=after_judgment.confidence,
            locate=located,
            screenshot=before_shot,
            after_screenshot=after_shot,
            debug_marked_image=debug_path,
        )

    return PrivacyCheckboxEnsureResult(
        action="failed",
        message=(
            f"[PrivacyCheckbox] FAILED — tap did not verify checkbox "
            f"vision={after_judgment.state} conf={after_judgment.confidence:.2f} "
            f"roi_diff={diff:.1f} threshold={change_threshold}. "
            f"{after_judgment.reason} "
            f"{located.format_message(prefix='[PrivacyCheckbox]')} "
            f"before={before_shot.resolve()} after={after_shot.resolve()}"
        ),
        tapped=True,
        verified=False,
        roi_diff=diff,
        vision_state=after_judgment.state,
        vision_confidence=after_judgment.confidence,
        locate=located,
        screenshot=before_shot,
        after_screenshot=after_shot,
        debug_marked_image=debug_path,
    )


def ensure_privacy_checkbox_checked(
    adb: AdbService,
    artifact_root: Path,
    *,
    step: int | None = None,
    prefix: str = "privacy_cb",
    already_tapped: bool = False,
    max_steps: int = _MAX_TAP_STEPS,
    change_threshold: float = _ROI_CHANGE_THRESHOLD,
    molmopoint_cfg: MolmopointSection | None = None,
) -> PrivacyCheckboxEnsureResult:
    """
    截图 OCR → 协议行左推坐标 → 点击 → ROI 前后对比验证变化。
    未找到协议行 → skipped。已 verified 过 → skipped。
    step 指定时只尝试该 step；否则从 0 递增直到 ROI 变化或步数用尽。
    """
    if already_tapped:
        return PrivacyCheckboxEnsureResult(
            action="skipped",
            message="[PrivacyCheckbox] SKIPPED — already verified this session.",
            tapped=False,
            verified=True,
        )

    ts = datetime.now().strftime("%H%M%S_%f")
    before_shot = artifact_root / f"{prefix}_before_{ts}.png"
    adb.screencap_png(before_shot)
    sw, sh = adb.touch_size()
    bboxes = extract_text_with_bbox(before_shot, device_w=sw, device_h=sh)
    if not bboxes:
        return PrivacyCheckboxEnsureResult(
            action="skipped",
            message="[PrivacyCheckbox] SKIPPED — OCR found no text.",
            screenshot=before_shot,
        )

    if find_privacy_terms_anchor(bboxes) is None:
        return PrivacyCheckboxEnsureResult(
            action="skipped",
            message="[PrivacyCheckbox] SKIPPED — no matching terms/agree line in OCR.",
            screenshot=before_shot,
        )

    steps_to_try = [step] if step is not None else list(range(max(0, max_steps) + 1))

    last_located: CheckboxLocateResult | None = None
    last_diff = 0.0
    last_after: Path | None = None

    for try_step in steps_to_try:
        located = _locate_for_tap_attempt(
            bboxes,
            before_shot=before_shot,
            sw=sw,
            sh=sh,
            molmopoint_cfg=molmopoint_cfg,
            step=try_step,
            try_molmopoint=(try_step == 0),
        )
        if located is None:
            continue
        last_located = located
        box = checkbox_roi_box(located, sw, sh)
        tap_msg = adb.tap(located.cx, located.cy, width=sw, height=sh)
        adb.wait_seconds(0.4)
        after_ts = datetime.now().strftime("%H%M%S_%f")
        after_shot = artifact_root / f"{prefix}_after_s{try_step}_{after_ts}.png"
        adb.screencap_png(after_shot)
        last_after = after_shot
        diff = roi_mean_abs_diff(before_shot, after_shot, box)
        last_diff = diff
        if diff >= change_threshold:
            return PrivacyCheckboxEnsureResult(
                action="tapped",
                message=(
                    f"[PrivacyCheckbox] VERIFIED {tap_msg} roi_diff={diff:.1f} "
                    f"(threshold={change_threshold}) "
                    f"{located.format_message(prefix='[PrivacyCheckbox]')} "
                    f"before={before_shot.resolve()} after={after_shot.resolve()}"
                ),
                tapped=True,
                verified=True,
                roi_diff=diff,
                locate=located,
                screenshot=before_shot,
                after_screenshot=after_shot,
            )

    if last_located is None:
        return PrivacyCheckboxEnsureResult(
            action="failed",
            message="[PrivacyCheckbox] FAILED — could not derive checkbox coordinates.",
            screenshot=before_shot,
        )

    return PrivacyCheckboxEnsureResult(
        action="failed",
        message=(
            f"[PrivacyCheckbox] FAILED — tap(s) did not change checkbox ROI "
            f"(last roi_diff={last_diff:.1f}, threshold={change_threshold}). "
            f"{last_located.format_message(prefix='[PrivacyCheckbox]')} "
            f"before={before_shot.resolve()}"
            + (f" after={last_after.resolve()}" if last_after else "")
        ),
        tapped=True,
        verified=False,
        roi_diff=last_diff,
        locate=last_located,
        screenshot=before_shot,
        after_screenshot=last_after,
    )
