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

from game_agent.i18n import Concept, compile_lexicon_pattern, phrase_in_text, text_contains

_COMPOUND_LOGIN_RE = compile_lexicon_pattern(
    Concept.COMPOUND_LOGIN,
    Concept.FORGOT_PASSWORD,
)
_STANDALONE_LOGIN_RE = re.compile(
    r"^(?:" + compile_lexicon_pattern(Concept.LOGIN_BUTTON).pattern + r")\s*[.!…]*$",
    re.IGNORECASE,
)
_ACCOUNT_RE = compile_lexicon_pattern(Concept.ACCOUNT_LABEL)
_PASSWORD_LABEL_RE = re.compile(
    r"^(?:" + compile_lexicon_pattern(Concept.PASSWORD_LABEL).pattern + r")\s*$",
    re.IGNORECASE,
)
_PASSWORD_HINT_RE = compile_lexicon_pattern(Concept.PASSWORD_HINT)

_EMAIL_VALUE_RE = re.compile(r"@[\w.-]+\.\w+")
_ACCOUNT_PLACEHOLDER_RE = re.compile(
    r"账号.*(?:手机|邮箱|phone|email)|(?:手机|邮箱).*账号",
    re.IGNORECASE,
)


def _field_tap_from_placeholder(line: OcrLine, *, screen_height: int) -> tuple[int, int]:
    """占位符 OCR 中心偏下，更接近 WebView 输入区。"""
    h = max(1, int(screen_height))
    dy = min(40, max(14, int(h * 0.028)))
    return line.x, line.y + dy


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
    if re.search(r"(?i)login|登录|登入|登錄", t) and len(t.split()) > 2:
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
        elif _ACCOUNT_PLACEHOLDER_RE.search(text) and account_line is None:
            account_line = line

        if _PASSWORD_LABEL_RE.match(text):
            password_line = line
        elif _PASSWORD_HINT_RE.search(text) or lower.strip() == "login password":
            password_hint_lines.append(line)

        if account_line is None and _EMAIL_VALUE_RE.search(text):
            account_line = line

        if is_standalone_login_label(text) and not is_compound_login_label(text):
            login_lines.append(line)

    password_xy: tuple[int, int] | None = None
    password_text = ""
    if password_line is not None:
        password_xy = _field_tap_from_placeholder(password_line, screen_height=h)
        password_text = password_line.text
    elif password_hint_lines:
        hint = password_hint_lines[0]
        below = [
            ln
            for ln in lines
            if ln.y > hint.y + int(h * 0.02)
            and ln.y < hint.y + int(h * 0.15)
            and abs(ln.x - hint.x) < max(80, int(h * 0.12))
            and not _PASSWORD_HINT_RE.search(ln.text)
        ]
        if below:
            field = min(below, key=lambda ln: ln.y)
            password_xy = (field.x, field.y)
            password_text = field.text or hint.text
        else:
            password_xy = _field_tap_from_placeholder(hint, screen_height=h)
            password_text = hint.text

    account_xy: tuple[int, int] | None = None
    account_text = ""
    if account_line is not None:
        account_xy = _field_tap_from_placeholder(account_line, screen_height=h)
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
    screen_width: int,
    screen_height: int,
    tag: str = "login",
) -> tuple[LoginFormOcrTargets, Path, str]:
    """设备截屏 → OCR → 解析登录表单坐标。"""
    ts = datetime.now().strftime("%H%M%S_%f")
    path = artifact_root / f"{tag}_ocr_{ts}.png"
    adb.screencap_png(path)
    from game_agent.utils.screen_coord import resolve_screen_coord_space

    space = resolve_screen_coord_space(adb, path)
    screen_width, screen_height = space.tap_w, space.tap_h
    raw = extract_text_with_bounds(path, device_w=screen_width, device_h=screen_height)
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
