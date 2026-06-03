"""
收起安全键盘后的 live screencap OCR，解析主 Login/登录 按钮坐标。

账号/密码填写阶段有安全键盘，截屏 OCR 不可靠，须用无障碍 setText；
仅在 dismiss 键盘、界面可见后再 OCR 定位登录按钮。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from game_agent.utils.ocr_util import OcrLine, extract_text_with_bounds, parse_ocr_lines

if TYPE_CHECKING:
    from game_agent.services.adb_service import AdbService

_COMPOUND_LOGIN_RE = re.compile(
    r"(/|\\|with|sign\s*up|signup|register|注册|forgot|忘记|第三方|"
    r"google|facebook|apple|wechat|qq|微博|手机验证码|login/sign)",
    re.IGNORECASE,
)

_STANDALONE_LOGIN_RE = re.compile(
    r"^(登录|立即登录|log\s*in|login)\s*[.!…]*$",
    re.IGNORECASE,
)

_ACCOUNT_RE = re.compile(
    r"(account|账号|用户名|邮箱|手机|email|cell\s*phone|手机号)",
    re.IGNORECASE,
)

_PASSWORD_LABEL_RE = re.compile(r"^(password|密码)\s*$", re.IGNORECASE)

_PASSWORD_HINT_RE = re.compile(
    r"(enter.*password|输入.*密码|please.*password)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LoginFormOcrTargets:
    account_xy: tuple[int, int] | None = None
    password_xy: tuple[int, int] | None = None
    login_button_xy: tuple[int, int] | None = None
    account_text: str = ""
    password_text: str = ""
    login_text: str = ""


def is_compound_login_label(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 24:
        return True
    if _COMPOUND_LOGIN_RE.search(t):
        return True
    if _STANDALONE_LOGIN_RE.match(t):
        return False
    if re.search(r"(?i)login|登录", t) and len(t.split()) > 2:
        return True
    return len(t) > 12


def is_standalone_login_label(text: str) -> bool:
    return bool(_STANDALONE_LOGIN_RE.match(text.strip()))


def resolve_login_form_targets(
    ocr_body: str,
    *,
    screen_height: int,
) -> LoginFormOcrTargets:
    """从 OCR 正文解析账号框、密码框、主 Login 按钮中心坐标。"""
    lines = parse_ocr_lines(ocr_body)
    if not lines:
        return LoginFormOcrTargets()

    h = max(1, int(screen_height))
    account_line: OcrLine | None = None
    password_line: OcrLine | None = None
    password_hint_lines: list[OcrLine] = []
    login_lines: list[OcrLine] = []

    for line in lines:
        text = line.text.strip()
        if not text or text.startswith("[OCR"):
            continue
        lower = text.lower()

        if _ACCOUNT_RE.search(text) and "sign up" not in lower and "signup" not in lower:
            if account_line is None or (
                "account" in lower and "account" not in (account_line.text or "").lower()
            ):
                account_line = line

        if _PASSWORD_LABEL_RE.match(text):
            password_line = line
        elif _PASSWORD_HINT_RE.search(text):
            password_hint_lines.append(line)

        if is_standalone_login_label(text) and not is_compound_login_label(text):
            login_lines.append(line)

    password_xy: tuple[int, int] | None = None
    password_text = ""
    if password_line is not None:
        password_xy = (password_line.x, password_line.y)
        password_text = password_line.text
    elif password_hint_lines:
        # 「Enter password」类占位：取密码区多条的中心
        avg_x = sum(l.x for l in password_hint_lines) // len(password_hint_lines)
        avg_y = sum(l.y for l in password_hint_lines) // len(password_hint_lines)
        password_xy = (avg_x, avg_y)
        password_text = password_hint_lines[0].text

    account_xy: tuple[int, int] | None = None
    account_text = ""
    if account_line is not None:
        account_xy = (account_line.x, account_line.y)
        account_text = account_line.text

    password_y = password_xy[1] if password_xy else None
    login_xy, login_text = _pick_login_button_xy(login_lines, screen_height=h, password_y=password_y)

    return LoginFormOcrTargets(
        account_xy=account_xy,
        password_xy=password_xy,
        login_button_xy=login_xy,
        account_text=account_text,
        password_text=password_text,
        login_text=login_text,
    )


def _pick_login_button_xy(
    login_lines: list[OcrLine],
    *,
    screen_height: int,
    password_y: int | None,
) -> tuple[tuple[int, int] | None, str]:
    if not login_lines:
        return None, ""

    best: OcrLine | None = None
    best_score = -1.0
    h = max(1, screen_height)

    for line in login_lines:
        if is_compound_login_label(line.text):
            continue
        sc = 500.0 - len(line.text) * 3.0
        if password_y is not None:
            dy = line.y - password_y
            if dy < 80:
                sc -= 400.0
            elif 120 <= dy <= 950:
                sc += 250.0
            elif dy > 1100:
                sc -= 150.0
        if line.y > int(h * 0.92):
            sc -= 400.0
        if sc > best_score:
            best_score = sc
            best = line

    if best is None:
        return None, ""
    return (best.x, best.y), best.text


def capture_login_form_targets(
    adb: AdbService,
    artifact_root: Path,
    *,
    screen_height: int,
    tag: str = "login",
) -> tuple[LoginFormOcrTargets, Path, str]:
    """设备截屏 → OCR → 解析登录表单坐标。"""
    ts = datetime.now().strftime("%H%M%S_%f")
    path = artifact_root / f"{tag}_ocr_{ts}.png"
    adb.screencap_png(path)
    raw = extract_text_with_bounds(path)
    targets = resolve_login_form_targets(raw, screen_height=screen_height)
    summary = format_targets_summary(targets, screencap=path)
    return targets, path, summary


def format_targets_summary(targets: LoginFormOcrTargets, *, screencap: Path) -> str:
    parts = [f"[Login form OCR] screencap={screencap.resolve()}"]
    if targets.account_xy:
        parts.append(
            f"  account=({targets.account_xy[0]},{targets.account_xy[1]}) "
            f"'{targets.account_text[:48]}'"
        )
    else:
        parts.append("  account=(not found)")
    if targets.password_xy:
        parts.append(
            f"  password=({targets.password_xy[0]},{targets.password_xy[1]}) "
            f"'{targets.password_text[:48]}'"
        )
    else:
        parts.append("  password=(not found)")
    if targets.login_button_xy:
        parts.append(
            f"  login_button=({targets.login_button_xy[0]},{targets.login_button_xy[1]}) "
            f"'{targets.login_text[:32]}'"
        )
    else:
        parts.append("  login_button=(not found)")
    return "\n".join(parts)
