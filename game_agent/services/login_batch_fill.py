"""登录表单原子填表：一次 OCR 取坐标 → 连续 u2 填账号密码 → Enter → OCR 验收。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from game_agent.services.accessibility_input import (
    fill_credential_via_accessibility,
    fill_password_via_accessibility,
    get_last_password_center_y,
    submit_login_after_password,
)
from game_agent.services.login_form_ocr import (
    LoginFormOcrTargets,
    capture_login_form_targets,
)
from game_agent.services.login_stage_probe import LoginProbeStage, probe_login_stage
from game_agent.utils.ocr_util import run_ocr_frame

if TYPE_CHECKING:
    from game_agent.models.settings import ExecutorSection


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


def ocr_login_field_coords(
    adb: Any,
    *,
    artifact_root: Path,
    screen_width: int,
    screen_height: int,
    tag: str = "atomic_login_ocr",
) -> OcrLoginCoordsResult:
    """登录页可见时截屏 OCR，解析账号/密码/登录按钮坐标。"""
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
) -> FillLoginAtCoordsResult:
    """纯填表：坐标入参，连续 u2 setText，中间不 OCR。"""
    sw, sh = screen_width, screen_height
    settle = min(executor.credential_fill_settle_s, 0.3)
    parts: list[str] = []
    errors: list[str] = []

    if username:
        if account_xy is None:
            errors.append("no account field coords")
        else:
            ax, ay = account_xy
            user_msg = fill_credential_via_accessibility(
                serial,
                ax,
                ay,
                username,
                width=sw,
                height=sh,
                field_label="username",
                settle_s=settle,
                verify_after_fill=False,
                max_center_distance_px=executor.credential_fill_max_distance_px,
                retry_on_verify_fail=False,
            )
            if "Accessibility fill failed" in user_msg:
                errors.append(user_msg[:300])
            else:
                parts.append(user_msg[:200])

    if password:
        if password_xy is not None:
            px, py = password_xy
            pass_msg = fill_credential_via_accessibility(
                serial,
                px,
                py,
                password,
                width=sw,
                height=sh,
                field_label="password",
                settle_s=settle,
                verify_after_fill=False,
                max_center_distance_px=executor.credential_fill_max_distance_px,
                retry_on_verify_fail=False,
            )
        else:
            pass_msg = fill_password_via_accessibility(
                serial,
                password,
                width=sw,
                height=sh,
                settle_s=settle,
                verify_after_fill=False,
                max_center_distance_px=executor.credential_fill_max_distance_px,
            )

        if "Accessibility fill failed" in pass_msg or "password-same-as-username" in pass_msg:
            errors.append(pass_msg[:300])
        else:
            parts.append(pass_msg[:200])

    if errors:
        return FillLoginAtCoordsResult(ok=False, message="; ".join(errors))
    return FillLoginAtCoordsResult(ok=True, message=" | ".join(parts))


def submit_login_after_fill(
    serial: str | None,
    adb: Any,
    *,
    executor: ExecutorSection,
    artifact_root: Path,
    screen_width: int,
    screen_height: int,
    cached_login_xy: tuple[int, int] | None = None,
) -> tuple[bool, str]:
    """填表后提交：Enter + u2 登录按钮 / 缓存坐标。"""
    sw, sh = screen_width, screen_height
    settle = min(executor.credential_fill_settle_s, 0.3)
    msg = submit_login_after_password(
        serial,
        adb,
        width=sw,
        height=sh,
        cached_login_xy=cached_login_xy,
        password_y=get_last_password_center_y(serial),
        artifact_root=artifact_root,
        screen_height=sh,
        settle_s=settle,
        press_enter=executor.login_submit_press_enter,
        use_cached_coords=executor.use_cached_login_button_xy,
        try_dismiss=executor.dismiss_keyboard_after_password,
        press_back_on_dismiss=executor.dismiss_keyboard_press_back,
    )
    ok = "failed" not in msg.lower()
    return ok, msg


def verify_login_with_ocr(
    adb: Any,
    *,
    executor: ExecutorSection,
    artifact_root: Path,
    screen_width: int,
    screen_height: int,
    tag: str = "atomic_login_verify",
) -> VerifyLoginOcrResult:
    """OCR 验收：黑屏则收键盘/BACK 后再 OCR；判断是否离开 login_form。"""
    from game_agent.services.login_secure_keyboard import (
        is_login_secure_keyboard_blackout,
        try_dismiss_login_secure_keyboard,
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
    """顶层原子登录：OCR → 填表 → 提交 → OCR 验收。"""
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

    fill_result = fill_login_at_coords(
        serial,
        account_xy=targets.account_xy,
        password_xy=targets.password_xy,
        username=username,
        password=password,
        executor=executor,
        screen_width=sw,
        screen_height=sh,
    )
    parts.append(fill_result.message)
    if not fill_result.ok:
        return AtomicLoginResult(
            ok=False,
            message="\n".join(parts),
            stage="login_form",
            targets=targets,
            left_login_form=False,
        )

    login_xy = targets.login_button_xy or cached_login_xy
    submit_ok, submit_msg = submit_login_after_fill(
        serial,
        adb,
        executor=executor,
        artifact_root=artifact_root,
        screen_width=sw,
        screen_height=sh,
        cached_login_xy=login_xy,
    )
    parts.append(submit_msg[:400])
    if not submit_ok:
        return AtomicLoginResult(
            ok=False,
            message="\n".join(parts),
            stage="login_form",
            targets=targets,
            left_login_form=False,
        )

    adb.wait_seconds(0.5)
    verify = verify_login_with_ocr(
        adb,
        executor=executor,
        artifact_root=artifact_root,
        screen_width=sw,
        screen_height=sh,
    )
    parts.append(verify.message[:400])

    success_stage = verify.stage
    ok = verify.left_login_form
    return AtomicLoginResult(
        ok=ok,
        message="\n".join(parts),
        stage=success_stage,
        targets=targets,
        left_login_form=verify.left_login_form,
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
