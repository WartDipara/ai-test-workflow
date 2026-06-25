"""登录表单原子填表：OCR 定位账号框 → Enter 跳密码 → 提交按钮/ENTER 提交。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from game_agent.services.accessibility_input import (
    fill_login_with_enter_flow,
    submit_login_after_password,
)
from game_agent.services.login_form_ocr import (
    LoginFormOcrTargets,
    capture_login_form_targets,
)
from game_agent.services.login_secure_keyboard import try_dismiss_login_secure_keyboard
from game_agent.services.login_stage_probe import LoginProbeStage, probe_login_stage
from game_agent.utils.ocr_util import run_ocr_frame

if TYPE_CHECKING:
    from game_agent.models.settings import ExecutorSection

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OcrLoginCoordsResult:
    targets: LoginFormOcrTargets
    ocr_summary: str
    screenshot: Path


@dataclass(frozen=True, slots=True)
class FillLoginAtCoordsResult:
    ok: bool
    message: str


@dataclass(frozen=True, slots=True)
class VerifyLoginOcrResult:
    left_login_form: bool
    stage: LoginProbeStage
    message: str
    ocr_summary: str
    screenshot: Path
    dismiss_note: str = ""


@dataclass(frozen=True, slots=True)
class AtomicLoginResult:
    ok: bool
    message: str
    stage: LoginProbeStage = "clear"
    targets: LoginFormOcrTargets | None = None
    left_login_form: bool = False
    verify_screenshot: Path | None = None
    verify_ocr_summary: str = ""
    ocr_verify_ok: bool = False


def ocr_login_field_coords(
    adb: Any,
    *,
    artifact_root: Path,
    screen_width: int,
    screen_height: int,
    tag: str = "atomic_login_ocr",
) -> OcrLoginCoordsResult:
    """登录页可见时截屏 OCR，解析账号框坐标（密码框由 Enter 跳转，不必 OCR）。"""
    targets, shot, ocr_summary = capture_login_form_targets(
        adb,
        artifact_root,
        screen_width=screen_width,
        screen_height=screen_height,
        tag=tag,
    )
    account_xy = targets.account_xy or _guess_account_xy_from_filled_email(targets, ocr_summary)
    if account_xy is not None and targets.account_xy is None:
        targets = LoginFormOcrTargets(
            account_xy=account_xy,
            password_xy=targets.password_xy,
            login_button_xy=targets.login_button_xy,
            account_text=targets.account_text,
            password_text=targets.password_text,
            login_text=targets.login_text,
        )
    return OcrLoginCoordsResult(targets=targets, ocr_summary=ocr_summary, screenshot=shot)


def fill_login_at_coords(
    serial: str | None,
    *,
    account_xy: tuple[int, int] | None,
    password_xy: tuple[int, int] | None,
    username: str,
    password: str,
    executor: ExecutorSection,
    screen_width: int,
    screen_height: int,
    submit_via_enter: bool = False,
) -> FillLoginAtCoordsResult:
    """填账号 → Enter 跳密码 → 填密码；提交由 submit_login_after_password 负责。"""
    del password_xy
    settle = min(executor.credential_fill_settle_s, 0.3)
    ok, message = fill_login_with_enter_flow(
        serial,
        account_xy=account_xy,
        username=username,
        password=password,
        width=screen_width,
        height=screen_height,
        settle_s=settle,
        submit_via_enter=submit_via_enter,
    )
    return FillLoginAtCoordsResult(ok=ok, message=message)


def verify_login_with_ocr(
    adb: Any,
    *,
    executor: ExecutorSection,
    artifact_root: Path,
    screen_width: int,
    screen_height: int,
    tag: str = "atomic_login_verify",
) -> VerifyLoginOcrResult:
    """OCR 验收：黑屏或键盘遮挡则收键盘后再 OCR；判断是否离开 login_form。"""
    from game_agent.services.login_secure_keyboard import (
        is_login_secure_keyboard_blackout,
    )

    sw, sh = screen_width, screen_height
    ts = datetime.now().strftime("%H%M%S_%f")
    shot = artifact_root / f"{tag}_{ts}.png"
    adb.screencap_png(shot)
    summary, bboxes = run_ocr_frame(
        shot, device_w=sw, device_h=sh, worker_key=adb.device_serial,
    )
    dismiss_note = ""

    if is_login_secure_keyboard_blackout(shot, bboxes, ocr_summary=summary):
        dismiss_note = try_dismiss_login_secure_keyboard(adb, executor)
        adb.wait_seconds(0.5)
        ts2 = datetime.now().strftime("%H%M%S_%f")
        shot = artifact_root / f"{tag}_after_dismiss_{ts2}.png"
        adb.screencap_png(shot)
        summary, bboxes = run_ocr_frame(
            shot, device_w=sw, device_h=sh, worker_key=adb.device_serial,
        )

    probe = probe_login_stage(bboxes, screen_w=sw, screen_h=sh)
    if probe.stage == "login_form" and executor.dismiss_keyboard_after_password:
        extra = try_dismiss_login_secure_keyboard(adb, executor)
        if extra:
            dismiss_note = f"{dismiss_note}; {extra}" if dismiss_note else extra
            adb.wait_seconds(0.5)
            ts3 = datetime.now().strftime("%H%M%S_%f")
            shot = artifact_root / f"{tag}_after_keyboard_{ts3}.png"
            adb.screencap_png(shot)
            summary, bboxes = run_ocr_frame(
                shot, device_w=sw, device_h=sh, worker_key=adb.device_serial,
            )
            probe = probe_login_stage(bboxes, screen_w=sw, screen_h=sh)

    left_login_form = probe.stage != "login_form"
    msg = probe.format_hint()
    if dismiss_note:
        msg = f"dismiss={dismiss_note[:200]}; {msg}"
    return VerifyLoginOcrResult(
        left_login_form=left_login_form,
        stage=probe.stage,
        message=msg,
        ocr_summary=summary,
        screenshot=shot,
        dismiss_note=dismiss_note,
    )


def atomic_login_fill_and_submit(
    adb: Any,
    *,
    username: str,
    password: str,
    executor: ExecutorSection,
    artifact_root: Path,
    screen_width: int,
    screen_height: int,
    cached_login_xy: tuple[int, int] | None = None,
) -> AtomicLoginResult:
    """顶层原子登录：OCR 定位 → Enter 流填表 → submit_login_after_password → OCR 验收。"""
    sw, sh = screen_width, screen_height
    serial = adb.device_serial

    ocr_result = ocr_login_field_coords(
        adb,
        artifact_root=artifact_root,
        screen_width=sw,
        screen_height=sh,
    )
    targets = ocr_result.targets
    parts = [ocr_result.ocr_summary[:400]]

    login_xy = targets.login_button_xy or cached_login_xy
    password_y = targets.password_xy[1] if targets.password_xy else None

    fill_result = fill_login_at_coords(
        serial,
        account_xy=targets.account_xy,
        password_xy=None,
        username=username,
        password=password,
        executor=executor,
        screen_width=sw,
        screen_height=sh,
        submit_via_enter=False,
    )
    parts.append(fill_result.message)
    logger.info("[atomic_login] fill: %s", fill_result.message)
    if not fill_result.ok:
        return AtomicLoginResult(
            ok=False,
            message="\n".join(parts),
            stage="login_form",
            targets=targets,
            left_login_form=False,
            ocr_verify_ok=False,
        )

    settle = min(executor.credential_fill_settle_s, 0.4)
    submit_msg = submit_login_after_password(
        serial,
        adb,
        width=sw,
        height=sh,
        cached_login_xy=login_xy,
        password_y=password_y,
        artifact_root=artifact_root,
        screen_height=sh,
        settle_s=settle,
        press_enter=executor.login_submit_press_enter,
        use_cached_coords=executor.use_cached_login_button_xy,
        try_dismiss=executor.dismiss_keyboard_after_password,
        press_back_on_dismiss=executor.dismiss_keyboard_press_back,
    )
    parts.append(submit_msg)
    logger.info("[atomic_login] submit: %s", submit_msg.replace("\n", " | "))

    if executor.dismiss_keyboard_after_password:
        dismiss_msg = try_dismiss_login_secure_keyboard(adb, executor)
        if dismiss_msg:
            parts.append(f"pre-verify dismiss: {dismiss_msg[:300]}")
            logger.info("[atomic_login] pre-verify dismiss: %s", dismiss_msg[:300])

    adb.wait_seconds(0.5)
    verify = verify_login_with_ocr(
        adb,
        executor=executor,
        artifact_root=artifact_root,
        screen_width=sw,
        screen_height=sh,
    )
    parts.append(verify.message[:400])

    ocr_ok = verify.left_login_form
    return AtomicLoginResult(
        ok=ocr_ok,
        message="\n".join(parts),
        stage=verify.stage,
        targets=targets,
        left_login_form=verify.left_login_form,
        verify_screenshot=verify.screenshot,
        verify_ocr_summary=verify.ocr_summary,
        ocr_verify_ok=ocr_ok,
    )


def _guess_account_xy_from_filled_email(
    targets: LoginFormOcrTargets,
    ocr_body: str,
) -> tuple[int, int] | None:
    """已填入邮箱时 OCR 行可能无 Account 标签，用 @ 行作点击参考。"""
    from game_agent.utils.ocr_util import parse_ocr_lines

    if targets.account_xy is not None:
        return targets.account_xy
    for line in parse_ocr_lines(ocr_body):
        text = line.text.strip()
        if "@" in text and "." in text and len(text) > 5:
            return (line.x, line.y)
    return None
