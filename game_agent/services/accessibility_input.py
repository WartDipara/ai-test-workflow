from __future__ import annotations

import logging
import math
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EDIT_TEXT_CLASS_PATTERN = ".*(EditText|AutoCompleteTextView).*"

_MIN_PASSWORD_BELOW_USERNAME_PX = 48
_SAME_FIELD_CENTER_PX = 55


def _login_fill_log(event: str, **fields: object) -> None:
    """诊断日志：fill_path / edits_count 等，便于复盘 WebView 登录填表路径。"""
    parts = [f"[LoginFill] {event}"]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger.info(" ".join(parts))


def _format_xy(xy: tuple[int, int] | None) -> str:
    if xy is None:
        return "-"
    return f"({xy[0]},{xy[1]})"


_device_lock = threading.Lock()
_devices: dict[str, Any] = {}

_last_username_center: dict[str, tuple[int, int]] = {}
_last_password_center: dict[str, tuple[int, int]] = {}


def get_last_password_center_y(serial: str | None) -> int | None:
    c = _last_password_center.get(serial or "__default__")
    return c[1] if c else None


def _connect_u2(serial: str | None) -> Any:
    try:
        import uiautomator2 as u2
    except ImportError as e:
        raise RuntimeError(
            "uiautomator2 not installed. Run: pip install uiautomator2, "
            "then on device: python -m uiautomator2 init"
        ) from e

    key = serial or "__default__"
    with _device_lock:
        cached = _devices.get(key)
        if cached is not None:
            return cached
        logger.info("uiautomator2 connected serial=%s", serial or "(default)")
        device = u2.connect(serial) if serial else u2.connect()
        _devices[key] = device
        return device


def _bounds_center(bounds: dict[str, int]) -> tuple[int, int]:
    left = int(bounds.get("left", 0))
    top = int(bounds.get("top", 0))
    right = int(bounds.get("right", left))
    bottom = int(bounds.get("bottom", top))
    return (left + right) // 2, (top + bottom) // 2


def _enumerate_edits(device: Any) -> list[tuple[Any, int, int, dict[str, int]]]:
    """列出可见输入框，按 (cy, cx) 排序（上→下）。"""
    try:
        nodes = device(classNameMatches=_EDIT_TEXT_CLASS_PATTERN)
    except Exception:
        return []

    out: list[tuple[Any, int, int, dict[str, int]]] = []
    try:
        count = nodes.count
    except Exception:
        count = 0

    for i in range(count):
        try:
            el = nodes[i]
            if not el.exists:
                continue
            info = el.info or {}
            if not info.get("visible", True):
                continue
            bounds = info.get("bounds") or {}
            if not bounds:
                continue
            cx, cy = _bounds_center(bounds)
            out.append((el, cx, cy, bounds))
        except Exception:
            continue

    out.sort(key=lambda t: (t[2], t[1]))
    return out


def _center_too_close(
    cx: int, cy: int, other: tuple[int, int] | None, *, threshold: float
) -> bool:
    if other is None:
        return False
    return math.hypot(cx - other[0], cy - other[1]) < threshold


def _pick_nearest_editable(device: Any, x: int, y: int) -> Any | None:
    """按 OCR 坐标选最近的可见输入框。"""
    edits = _enumerate_edits(device)
    if not edits:
        return None
    best = min(edits, key=lambda t: math.hypot(t[1] - x, t[2] - y))
    return best[0]


def _pick_credential_edit(
    device: Any,
    ocr_x: int,
    ocr_y: int,
    field_label: str,
    *,
    username_center: tuple[int, int] | None = None,
) -> tuple[Any | None, str, int, int]:
    """
    选择要填写的 EditText，并返回建议点击坐标（节点中心，非 OCR 标签位置）。

    密码：优先选账号框下方、且中心不同于账号框的输入框；避免焦点仍停在账号框。
    """
    edits = _enumerate_edits(device)
    if not edits:
        return None, "none", ocr_x, ocr_y

    fl = (field_label or "field").strip().lower()
    is_password = fl == "password" or fl == "pwd"

    if is_password and len(edits) >= 2:
        below: list[tuple[Any, int, int, dict[str, int], float]] = []
        for el, cx, cy, bounds in edits:
            if username_center is not None:
                if cy < username_center[1] + _MIN_PASSWORD_BELOW_USERNAME_PX:
                    continue
                if _center_too_close(cx, cy, username_center, threshold=_SAME_FIELD_CENTER_PX):
                    continue
            dist = math.hypot(cx - ocr_x, cy - ocr_y)
            below.append((el, cx, cy, bounds, dist))

        if below:
            el, cx, cy, _b, _d = min(below, key=lambda t: t[4])
            return el, "password-below-username", cx, cy

        # 无「明显在下方」的框：取排序后第二个（常见双框登录表单）
        el, cx, cy, _b = edits[1]
        if username_center and _center_too_close(cx, cy, username_center, threshold=_SAME_FIELD_CENTER_PX):
            if len(edits) >= 3:
                el, cx, cy, _b = edits[2]
                return el, "password-edit-index-2", cx, cy
            return None, "password-same-as-username", ocr_x, ocr_y
        return el, "password-edit-index-1", cx, cy

    # 账号或其它：最近 OCR 点，但若与已填账号框重合则拒绝（防误用）
    best = min(edits, key=lambda t: math.hypot(t[1] - ocr_x, t[2] - ocr_y))
    el, cx, cy, _b = best
    if is_password and username_center and _center_too_close(cx, cy, username_center, threshold=_SAME_FIELD_CENTER_PX):
        if len(edits) >= 2:
            el, cx, cy, _b = edits[1]
            return el, "password-fallback-second", cx, cy
        return None, "password-same-as-username", ocr_x, ocr_y

    pick = "nearest EditText" if not is_password else "password-nearest"
    return el, pick, cx, cy


def _read_editable_text(target: Any) -> str:
    try:
        if hasattr(target, "get_text"):
            raw = target.get_text()
            if raw is not None:
                return str(raw).strip()
    except Exception:
        pass
    try:
        info = target.info or {}
        return str(info.get("text") or "").strip()
    except Exception:
        return ""


def _is_masked_secret(text: str) -> bool:
    if not text:
        return False
    mask = set("•●*·・●○◦▪●")
    return all(c in mask or c.isspace() for c in text)


def _node_center_distance(target: Any, tap_x: int, tap_y: int) -> tuple[int, int, float]:
    info = target.info or {}
    bounds = info.get("bounds") or {}
    cx, cy = _bounds_center(bounds)
    return cx, cy, math.hypot(cx - tap_x, cy - tap_y)


def verify_credential_node(
    target: Any,
    expected: str,
    *,
    field_label: str,
    tap_x: int,
    tap_y: int,
    max_center_distance_px: float,
    pick: str,
    username_center: tuple[int, int] | None = None,
) -> tuple[bool, str]:
    """校验填入的节点：位置是否对准点击点、文本是否与凭据一致（密码支持掩码长度）。"""
    cx, cy, dist = _node_center_distance(target, tap_x, tap_y)
    actual = _read_editable_text(target)
    info = target.info or {}
    fl = (field_label or "field").strip().lower()
    is_password = fl == "password" or fl == "pwd"
    is_pwd_field = bool(info.get("password")) or is_password

    lines: list[str] = [
        f"VERIFY pick={pick} node_center=({cx},{cy}) tap=({tap_x},{tap_y}) dist={dist:.0f}px",
    ]

    same_as_username = False
    if is_password and username_center is not None:
        udist = math.hypot(cx - username_center[0], cy - username_center[1])
        lines.append(
            f"VERIFY vs username center ({username_center[0]},{username_center[1]}): {udist:.0f}px"
        )
        if udist < _SAME_FIELD_CENTER_PX:
            same_as_username = True
            lines.append(
                "VERIFY password: FAIL — same EditText as username (focus did not move). "
                "Re-fill password after username; do not reuse username OCR coords."
            )

    position_ok = dist <= max_center_distance_px
    if position_ok:
        lines.append(f"VERIFY position: OK (within {max_center_distance_px:.0f}px)")
    else:
        lines.append(
            "VERIFY position: FAIL — node too far from tap; likely wrong field. Re-OCR both fields."
        )

    text_ok = False
    if same_as_username:
        text_ok = False
    elif fl == "username" or field_label == "username":
        if actual == expected:
            text_ok = True
            lines.append("VERIFY username: OK (exact match)")
        elif actual.lower() == expected.lower():
            text_ok = True
            lines.append("VERIFY username: OK (case-insensitive match)")
        elif not actual:
            lines.append("VERIFY username: FAIL — field empty after fill")
        else:
            preview = actual[:2] + "…" if len(actual) > 4 else "…"
            lines.append(
                f"VERIFY username: FAIL — node text '{preview}' does not match credentials "
                f"(len {len(actual)} vs expected {len(expected)})"
            )
    else:
        if actual == expected:
            text_ok = True
            lines.append("VERIFY password: OK (plaintext readable on node)")
        elif _is_masked_secret(actual) and len(actual) == len(expected):
            text_ok = True
            lines.append(f"VERIFY password: OK (masked, length {len(actual)})")
        elif actual and not _is_masked_secret(actual) and len(actual) == len(expected):
            text_ok = True
            lines.append(f"VERIFY password: OK (non-empty, length {len(actual)})")
        elif is_pwd_field and not actual:
            lines.append(
                "VERIFY password: PARTIAL — password field but text hidden in accessibility; "
                "after keyboard dismiss re-run atomic_login OCR verify"
            )
            text_ok = position_ok
        elif not actual:
            lines.append("VERIFY password: FAIL — field empty after fill")
        else:
            lines.append(
                f"VERIFY password: FAIL — len {len(actual)} vs expected {len(expected)} "
                "(masked mismatch or wrong field)"
            )

    passed = position_ok and text_ok and not same_as_username
    if passed:
        lines.append(f"VERIFY {field_label}: PASSED")
    elif not position_ok:
        lines.append(
            f"VERIFY {field_label}: FAILED — fix coordinates with OCR, then re-fill"
        )
    elif field_label == "password" and "PARTIAL" in "\n".join(lines):
        lines.append(f"VERIFY {field_label}: PARTIAL — proceed with caution")
    else:
        lines.append(f"VERIFY {field_label}: FAILED — re-fill or re-OCR")

    return passed, "\n".join(lines)


def verify_credential_via_accessibility(
    serial: str | None,
    x: int,
    y: int,
    expected: str,
    *,
    width: int,
    height: int,
    field_label: str = "field",
    max_center_distance_px: float = 150.0,
) -> str:
    """仅校验（不写入）：按字段规则选 EditText 并核对位置与文本。"""
    if not (0 <= x < width and 0 <= y < height):
        return f"Refused: ({x},{y}) outside {width}x{height}"

    device = _connect_u2(serial)
    key = serial or "__default__"
    username_center = _last_username_center.get(key)
    target, pick, tap_x, tap_y = _pick_credential_edit(
        device, x, y, field_label, username_center=username_center
    )
    if target is None:
        return (
            f"VERIFY FAIL: no suitable EditText for {field_label} near ({x},{y}). "
            "Fill username first; re-run OCR for field centers."
        )
    passed, block = verify_credential_node(
        target,
        expected,
        field_label=field_label,
        tap_x=tap_x,
        tap_y=tap_y,
        max_center_distance_px=max_center_distance_px,
        pick=pick,
        username_center=username_center,
    )
    return block


def _focused_editable(device: Any) -> Any | None:
    try:
        el = device(focused=True)
        if el.exists:
            info = el.info or {}
            cls = str(info.get("className", ""))
            if "EditText" in cls or "AutoComplete" in cls or info.get("editable"):
                return el
    except Exception:
        pass
    return None


def _clear_editable_field(target: Any, device: Any, *, aggressive: bool = False) -> None:
    """
    写入前清空输入框。默认仅用无障碍 clear/setText；仅在仍非空时用少量 DEL 兜底。
    """
    if hasattr(target, "clear_text"):
        try:
            target.clear_text()
            time.sleep(0.05)
        except Exception as e:
            logger.debug("clear_text: %s", e)

    if hasattr(target, "set_text"):
        try:
            target.set_text("")
            time.sleep(0.05)
        except Exception as e:
            logger.debug("set_text empty: %s", e)

    if _read_editable_text(target):
        try:
            target.click()
            time.sleep(0.06)
        except Exception:
            pass

    remaining = _read_editable_text(target)
    if not remaining and not aggressive:
        return

    # 安全键盘等场景：无障碍清空无效时，按已有文本长度有限次删除（避免 96 次 DEL 长时间「删空」）
    delete_budget = min(max(len(remaining) + 4, 8), 24) if remaining else (8 if aggressive else 0)
    if delete_budget <= 0:
        return

    try:
        device.shell("input keyevent 123")  # MOVE_END
        for _ in range(delete_budget):
            device.shell("input keyevent 67")  # DEL
        time.sleep(0.04)
    except Exception as e:
        logger.debug("keyevent clear: %s", e)


def _set_text_replace(target: Any, device: Any, text: str) -> None:
    """直接 set_text（多数机型会整框替换）。"""
    if hasattr(target, "set_text"):
        target.set_text(text)
        return
    if hasattr(target, "clear_text"):
        target.clear_text()
        device.send_keys(text)
        return
    device.send_keys(text)


def _clear_editable_field_u2(target: Any) -> None:
    """仅用 uiautomator2 清空输入框（登录填表路径，不发 DEL）。"""
    if hasattr(target, "clear_text"):
        try:
            target.clear_text()
        except Exception as e:
            logger.debug("clear_text: %s", e)
    if hasattr(target, "set_text"):
        try:
            target.set_text("")
        except Exception as e:
            logger.debug("set_text empty: %s", e)


def fill_edit_text_u2(target: Any, device: Any, text: str) -> None:
    """先清空栏位再写入，统一走 uiautomator2 set_text。"""
    _clear_editable_field_u2(target)
    if hasattr(target, "set_text"):
        target.set_text(text)
        return
    device.send_keys(text)


def press_enter_key(device: Any, *, settle_s: float = 0.25) -> str:
    """单次 Enter（IME 下一项 / 提交）。"""
    return _press_enter_submit(device, settle_s)


def fill_login_with_ocr_tap_fallback(
    adb: Any,
    *,
    serial: str | None,
    account_xy: tuple[int, int],
    password_xy: tuple[int, int] | None,
    username: str,
    password: str,
    width: int,
    height: int,
    settle_s: float = 0.35,
) -> tuple[bool, str]:
    """WebView 无 EditText 节点时：OCR 坐标 tap + u2 setText（adb 粘贴兜底）。"""
    device = _connect_u2(serial)
    edits_count = len(_enumerate_edits(device))
    webview_no_edits = edits_count == 0
    _login_fill_log(
        "route",
        fill_path="ocr-hybrid",
        edits_count=edits_count,
        account_xy=_format_xy(account_xy),
        password_xy=_format_xy(password_xy),
        screen=f"{width}x{height}",
    )

    ax, ay = account_xy
    if not (0 <= ax < width and 0 <= ay < height):
        return False, f"account coords ({ax},{ay}) outside {width}x{height}"

    wait = max(0.15, min(float(settle_s), 0.6))
    parts: list[str] = []
    key = serial or "__default__"

    ok_acc, acc_msg, acc_center = _fill_credential_at_ocr_coord(
        adb,
        serial,
        ax,
        ay,
        username,
        width=width,
        height=height,
        settle_s=wait,
        field_label="username",
        webview_no_edits=webview_no_edits,
    )
    parts.append(f"account: {acc_msg}")
    if not ok_acc:
        _login_fill_log("done", fill_path="ocr-hybrid", ok=False, stage="account")
        return False, "fill_path=ocr-hybrid | " + " | ".join(parts)
    if acc_center is not None:
        _last_username_center[key] = acc_center

    if password_xy is not None:
        px, py = password_xy
    else:
        px, py = ax, ay + max(_MIN_PASSWORD_BELOW_USERNAME_PX, int(height * 0.10))
    if not (0 <= px < width and 0 <= py < height):
        return False, f"password coords ({px},{py}) outside {width}x{height}; " + " | ".join(parts)

    time.sleep(wait)
    ok_pwd, pwd_msg, pwd_center = _fill_credential_at_ocr_coord(
        adb,
        serial,
        px,
        py,
        password,
        width=width,
        height=height,
        settle_s=wait,
        field_label="password",
        username_center=_last_username_center.get(key),
        webview_no_edits=webview_no_edits,
    )
    parts.append(f"password: {pwd_msg}")
    if pwd_center is not None:
        _last_password_center[key] = pwd_center
    if not ok_pwd:
        _login_fill_log("done", fill_path="ocr-hybrid", ok=False, stage="password")
        return False, "fill_path=ocr-hybrid | " + " | ".join(parts)
    _login_fill_log("done", fill_path="ocr-hybrid", ok=True)
    return True, "fill_path=ocr-hybrid | " + " | ".join(parts)


def _try_enable_fastinput_ime(device: Any) -> None:
    try:
        device.set_fastinput_ime(True)
    except Exception as e:
        logger.debug("set_fastinput_ime: %s", e)


def _tap_at(adb: Any, device: Any, x: int, y: int, *, width: int, height: int, wait: float) -> str:
    tap_msg = adb.tap(x, y, width=width, height=height)
    time.sleep(wait)
    try:
        device.click(x, y)
    except Exception as e:
        logger.debug("u2 click (%s,%s): %s", x, y, e)
    time.sleep(wait)
    return tap_msg


def _attempt_ime_send_keys(
    adb: Any,
    device: Any,
    x: int,
    y: int,
    text: str,
    *,
    width: int,
    height: int,
    wait: float,
    taps: int = 2,
) -> tuple[bool, str]:
    """WebView 无 EditText 节点时：聚焦后用 IME send_keys（内部多走剪贴板粘贴）。"""
    tap_msg = ""
    for _ in range(max(1, taps)):
        tap_msg = adb.tap(x, y, width=width, height=height)
        time.sleep(wait)
        try:
            device.click(x, y)
        except Exception:
            pass
        time.sleep(wait)
    _try_enable_fastinput_ime(device)
    try:
        device.send_keys(text)
        return True, f"{tap_msg} ime-send_keys"
    except Exception as e:
        return False, f"{tap_msg} ime-send_keys failed: {e!s}"


def _attempt_light_paste(
    adb: Any,
    device: Any,
    x: int,
    y: int,
    text: str,
    *,
    width: int,
    height: int,
    wait: float,
) -> tuple[bool, str]:
    """点击聚焦后直接粘贴，不做大批量 DEL 清空（WebView 易被清焦点）。"""
    tap_msg = _tap_at(adb, device, x, y, width=width, height=height, wait=wait)
    adb.tap(x, y, width=width, height=height)
    time.sleep(max(wait, 0.35))
    paste_msg = adb.paste_text(text)
    if "Pasted via clipboard" in paste_msg or "Typed via adb" in paste_msg:
        return True, f"{tap_msg} light-paste: {paste_msg[:60]}"
    return False, f"{tap_msg} light-paste failed: {paste_msg[:80]}"


def _fill_credential_at_ocr_coord(
    adb: Any,
    serial: str | None,
    x: int,
    y: int,
    text: str,
    *,
    width: int,
    height: int,
    settle_s: float,
    field_label: str,
    username_center: tuple[int, int] | None = None,
    webview_no_edits: bool = False,
) -> tuple[bool, str, tuple[int, int] | None]:
    """点击 OCR 坐标后优先 u2 setText；WebView 分屏登录常无法 adb 粘贴。"""
    wait = max(0.15, min(float(settle_s), 0.6))
    device = _connect_u2(serial)
    is_password = (field_label or "").strip().lower() in ("password", "pwd")

    tap_offsets: list[tuple[int, int, str]] = [
        (0, 0, "center"),
        (100, 0, "right100"),
        (180, 0, "right180"),
        (0, 24, "down24"),
        (120, 20, "right120-down20"),
        (60, 12, "right60"),
        (0, 48, "down48"),
        (-50, 20, "left50"),
    ]
    last_msg = ""

    for dx, dy, tag in tap_offsets:
        tx, ty = x + dx, y + dy
        if not (0 <= tx < width and 0 <= ty < height):
            continue
        tap_msg = _tap_at(adb, device, tx, ty, width=width, height=height, wait=wait)
        adb.tap(tx, ty, width=width, height=height)
        time.sleep(wait)

        focused = _focused_editable(device)
        if focused is not None:
            fill_edit_text_u2(focused, device, text)
            if _credential_fill_verified(focused, text, is_password=is_password):
                fcx, fcy, _ = _node_center_distance(focused, tx, ty)
                _login_fill_log(
                    "field",
                    field=field_label,
                    method="u2-focused",
                    offset=tag,
                    tap=f"({tx},{ty})",
                )
                return True, f"{tap_msg} u2-focused/{tag}", (fcx, fcy)
            last_msg = f"{tap_msg} focused-unverified/{tag}"

        target, pick, cx, cy = _pick_credential_edit(
            device,
            tx,
            ty,
            field_label,
            username_center=username_center,
        )
        if target is not None:
            try:
                target.click()
            except Exception:
                device.click(cx, cy)
            time.sleep(wait)
            active = _focused_editable(device) or target
            fill_edit_text_u2(active, device, text)
            if _credential_fill_verified(active, text, is_password=is_password):
                _login_fill_log(
                    "field",
                    field=field_label,
                    method=f"u2-{pick}",
                    offset=tag,
                    tap=f"({tx},{ty})",
                )
                return True, f"{tap_msg} u2-{pick}/{tag}", (cx, cy)
            last_msg = f"{tap_msg} u2-{pick}-unverified/{tag}"

        ime_ok, ime_msg = _attempt_ime_send_keys(
            adb,
            device,
            tx,
            ty,
            text,
            width=width,
            height=height,
            wait=wait,
            taps=1,
        )
        if ime_ok:
            focused_after = _focused_editable(device)
            if focused_after is not None and _credential_fill_verified(
                focused_after, text, is_password=is_password,
            ):
                fcx, fcy, _ = _node_center_distance(focused_after, tx, ty)
                _login_fill_log(
                    "field",
                    field=field_label,
                    method="ime-send_keys+verified",
                    offset=tag,
                    tap=f"({tx},{ty})",
                )
                return True, f"{ime_msg}/{tag}+verified", (fcx, fcy)
            if webview_no_edits:
                _login_fill_log(
                    "field",
                    field=field_label,
                    method="ime-send_keys",
                    offset=tag,
                    tap=f"({tx},{ty})",
                    note="webview_no_edits",
                )
                return True, f"{ime_msg}/{tag}", (tx, ty)

        paste_ok, paste_msg = _attempt_light_paste(
            adb,
            device,
            tx,
            ty,
            text,
            width=width,
            height=height,
            wait=wait,
        )
        if paste_ok:
            focused_after = _focused_editable(device)
            if focused_after is not None and _credential_fill_verified(
                focused_after, text, is_password=is_password,
            ):
                fcx, fcy, _ = _node_center_distance(focused_after, tx, ty)
                _login_fill_log(
                    "field",
                    field=field_label,
                    method="light-paste+verified",
                    offset=tag,
                    tap=f"({tx},{ty})",
                )
                return True, f"{paste_msg}/{tag}+verified", (fcx, fcy)
            if webview_no_edits:
                _login_fill_log(
                    "field",
                    field=field_label,
                    method="light-paste",
                    offset=tag,
                    tap=f"({tx},{ty})",
                    note="webview_no_edits",
                )
                return True, f"{paste_msg}/{tag}", (tx, ty)
            last_msg = f"{paste_msg} unverified/{tag}"

    ime_ok, ime_msg = _attempt_ime_send_keys(
        adb, device, x, y, text, width=width, height=height, wait=wait, taps=3,
    )
    if ime_ok and webview_no_edits:
        _login_fill_log(
            "field",
            field=field_label,
            method="ime-send_keys-final",
            tap=f"({x},{y})",
        )
        return True, ime_msg, (x, y)

    paste_ok, paste_msg = _attempt_light_paste(
        adb, device, x, y, text, width=width, height=height, wait=wait,
    )
    if paste_ok and webview_no_edits:
        _login_fill_log(
            "field",
            field=field_label,
            method="light-paste-final",
            tap=f"({x},{y})",
        )
        return True, paste_msg, (x, y)

    if last_msg:
        _login_fill_log(
            "field",
            field=field_label,
            method="all-failed",
            tap=f"({x},{y})",
        )
        return False, f"{last_msg}; final: {paste_msg}", None
    _login_fill_log(
        "field",
        field=field_label,
        method="all-failed",
        tap=f"({x},{y})",
    )
    return False, f"all methods failed; final: {paste_msg}", None


def _credential_fill_verified(target: Any, expected: str, *, is_password: bool) -> bool:
    actual = _read_editable_text(target)
    if not actual:
        return False
    if is_password:
        if actual == expected:
            return True
        if _is_masked_secret(actual) and len(actual) == len(expected):
            return True
        return len(actual) >= max(1, len(expected) // 2)
    if actual == expected or actual.lower() == expected.lower():
        return True
    return len(actual) >= max(3, len(expected) // 2)


def fill_login_with_enter_flow(
    serial: str | None,
    *,
    account_xy: tuple[int, int] | None,
    username: str,
    password: str,
    width: int,
    height: int,
    settle_s: float = 0.25,
    submit_via_enter: bool = False,
    adb: Any | None = None,
    password_xy: tuple[int, int] | None = None,
) -> tuple[bool, str]:
    """
    标准登录：点账号框 → 清空并填账号 → Enter 跳密码 → 清空并填密码。
    submit_via_enter=True 时再 Enter 提交；atomic_login 由 submit_login_after_password 单独提交。
    输入统一经 uiautomator2（先 clear_text 再 set_text）；不依赖密码框 OCR。
    """
    if not username.strip():
        return False, "username empty"
    if not password:
        return False, "password empty"

    device = _connect_u2(serial)
    key = serial or "__default__"
    wait = max(0.12, min(float(settle_s), 0.5))
    parts: list[str] = []

    edits = _enumerate_edits(device)
    _login_fill_log(
        "probe",
        edits_count=len(edits),
        account_xy=_format_xy(account_xy),
        password_xy=_format_xy(password_xy),
        screen=f"{width}x{height}",
    )
    if not edits and account_xy is None:
        return False, "fill_path=none | no visible EditText on login form"
    if not edits and account_xy is not None and adb is not None:
        _login_fill_log("route", fill_path="ocr-hybrid", reason="no_edits")
        return fill_login_with_ocr_tap_fallback(
            adb,
            serial=serial,
            account_xy=account_xy,
            password_xy=password_xy,
            username=username,
            password=password,
            width=width,
            height=height,
            settle_s=settle_s,
        )

    if account_xy is not None:
        ax, ay = account_xy
        if not (0 <= ax < width and 0 <= ay < height):
            return False, f"account coords ({ax},{ay}) outside {width}x{height}"
        account_el, pick, click_x, click_y = _pick_credential_edit(
            device, ax, ay, "username",
        )
        if account_el is None:
            if adb is not None and account_xy is not None:
                _login_fill_log(
                    "route",
                    fill_path="ocr-hybrid",
                    reason="no_pick_at_ocr",
                    edits_count=len(edits),
                )
                return fill_login_with_ocr_tap_fallback(
                    adb,
                    serial=serial,
                    account_xy=account_xy,
                    password_xy=password_xy,
                    username=username,
                    password=password,
                    width=width,
                    height=height,
                    settle_s=settle_s,
                )
            if not edits:
                return False, "no account EditText"
            account_el, click_x, click_y, _b = edits[0]
            pick = "first-edit-fallback"
    else:
        account_el, click_x, click_y, _b = edits[0]
        pick = "first-edit"

    _login_fill_log(
        "route",
        fill_path="u2-enter-flow",
        edits_count=len(edits),
        account_pick=pick,
    )

    try:
        account_el.click()
    except Exception:
        device.click(click_x, click_y)
    time.sleep(wait)

    focused = _focused_editable(device)
    if focused is not None:
        account_el = focused
        pick = f"{pick}+focused"

    fill_edit_text_u2(account_el, device, username)
    try:
        ucx, ucy, _ = _node_center_distance(account_el, click_x, click_y)
        _last_username_center[key] = (ucx, ucy)
    except Exception:
        _last_username_center[key] = (click_x, click_y)
    parts.append(f"account via {pick}")

    time.sleep(wait)
    parts.append(f"ENTER next-field: {press_enter_key(device, settle_s=wait)}")

    time.sleep(wait)
    pwd_el = _focused_editable(device)
    username_center = _last_username_center.get(key)
    if pwd_el is not None and username_center is not None:
        pcx, pcy, _ = _node_center_distance(pwd_el, click_x, click_y)
        if _center_too_close(pcx, pcy, username_center, threshold=_SAME_FIELD_CENTER_PX):
            pwd_el = None

    pwd_pick = "focused-after-enter"
    if pwd_el is None:
        hint_x = click_x
        hint_y = click_y + _MIN_PASSWORD_BELOW_USERNAME_PX
        if username_center is not None:
            hint_x, hint_y = username_center[0], username_center[1] + _MIN_PASSWORD_BELOW_USERNAME_PX
        pwd_el, pwd_pick, px, py = _pick_credential_edit(
            device,
            hint_x,
            hint_y,
            "password",
            username_center=username_center,
        )
        if pwd_el is None:
            _login_fill_log("done", fill_path="u2-enter-flow", ok=False, stage="password")
            return False, "fill_path=u2-enter-flow | password field not found after ENTER; " + " | ".join(parts)
        try:
            pwd_el.click()
        except Exception:
            device.click(px, py)
        time.sleep(wait)
        pwd_pick = f"{pwd_pick}@({px},{py})"

    fill_edit_text_u2(pwd_el, device, password)
    try:
        pcx, pcy, _ = _node_center_distance(pwd_el, click_x, click_y)
        _last_password_center[key] = (pcx, pcy)
    except Exception:
        pass
    parts.append(f"password via {pwd_pick}")

    if submit_via_enter:
        time.sleep(wait)
        parts.append(f"ENTER submit: {press_enter_key(device, settle_s=wait)}")

    _login_fill_log("done", fill_path="u2-enter-flow", ok=True, password_pick=pwd_pick)
    return True, f"fill_path=u2-enter-flow | " + " | ".join(parts)


def _apply_set_text(target: Any, device: Any, text: str, pick: str) -> tuple[str, str | None]:
    current = _read_editable_text(target)
    if current == text or (current and current.lower() == text.lower()):
        return f"{pick}+skip-unchanged", None

    _clear_editable_field(target, device)
    if _read_editable_text(target):
        _clear_editable_field(target, device, aggressive=True)

    try:
        if hasattr(target, "set_text") or hasattr(target, "clear_text"):
            _set_text_replace(target, device, text)
            return pick, None
        _clear_editable_field(target, device, aggressive=True)
        device.send_keys(text)
        return f"{pick}+send_keys", None
    except Exception as e:
        return pick, f"Accessibility set_text failed ({pick}): {e!s}"


def fill_credential_via_accessibility(
    serial: str | None,
    x: int,
    y: int,
    text: str,
    *,
    width: int,
    height: int,
    field_label: str = "field",
    settle_s: float = 0.4,
    verify_after_fill: bool = True,
    max_center_distance_px: float = 150.0,
    retry_on_verify_fail: bool = True,
) -> str:
    """
    通过无障碍（uiautomator2 / ACTION_SET_TEXT）填入凭据，不依赖 OCR 读屏或安全键盘画面。

    流程：点击 OCR 给出的框中心 → 聚焦节点 → clear + set_text。
    适用于小米/OPPO 等安全键盘（截图常全黑，但 EditText 节点仍可 setText）。
    """
    if not (0 <= x < width and 0 <= y < height):
        return f"Refused: ({x},{y}) outside {width}x{height}"

    device = _connect_u2(serial)
    key = serial or "__default__"
    fl = (field_label or "field").strip().lower()
    is_username = fl == "username" or fl == "account"
    username_center = None if is_username else _last_username_center.get(key)

    target, pick, click_x, click_y = _pick_credential_edit(
        device, x, y, field_label, username_center=username_center
    )
    if target is None:
        return (
            f"Accessibility fill failed: no suitable EditText for {field_label} at ({x},{y}). "
            "Fill username before password; ensure login form visible; run: python -m uiautomator2 init"
        )

    try:
        target.click()
    except Exception:
        device.click(click_x, click_y)
    time.sleep(max(0.15, min(float(settle_s), 2.0)))

    focused = _focused_editable(device)
    if focused is not None:
        try:
            fcx, fcy, _ = _node_center_distance(focused, click_x, click_y)
            if is_username or not _center_too_close(
                fcx, fcy, username_center, threshold=_SAME_FIELD_CENTER_PX
            ):
                target = focused
                pick = f"{pick}+focused"
        except Exception:
            pass

    existing = _read_editable_text(target)
    if existing == text or (existing and existing.lower() == text.lower()):
        parts = [
            f"Skipped {field_label} fill — field already contains expected value "
            f"({pick}).",
        ]
        if is_username:
            try:
                ucx, ucy, _ = _node_center_distance(target, click_x, click_y)
                _last_username_center[key] = (ucx, ucy)
            except Exception:
                pass
        elif not is_username:
            try:
                pcx, pcy, _ = _node_center_distance(target, click_x, click_y)
                _last_password_center[key] = (pcx, pcy)
            except Exception:
                pass
        if verify_after_fill:
            passed, verify_block = verify_credential_node(
                target,
                text,
                field_label=field_label,
                tap_x=click_x,
                tap_y=click_y,
                max_center_distance_px=max_center_distance_px,
                pick=pick,
                username_center=_last_username_center.get(key) if not is_username else None,
            )
            parts.append(verify_block)
            if not passed:
                parts.append(
                    "Action: re-OCR login screen → confirm field centers → fill again "
                    "or verify_credential_field."
                )
        return "\n".join(parts)

    pick, err = _apply_set_text(target, device, text, pick)
    if err:
        return err

    parts = [
        f"Filled {field_label} via accessibility (uiautomator2, {pick}) "
        f"ocr=({x},{y}) click=({click_x},{click_y}). "
        "Field cleared before set (replace, not append). Secure keyboard OK.",
    ]

    if not verify_after_fill:
        if is_username:
            try:
                ucx, ucy, _ = _node_center_distance(target, click_x, click_y)
                _last_username_center[key] = (ucx, ucy)
            except Exception:
                pass
        return parts[0]

    def _run_verify(el: Any, p: str) -> tuple[bool, str]:
        return verify_credential_node(
            el,
            text,
            field_label=field_label,
            tap_x=click_x,
            tap_y=click_y,
            max_center_distance_px=max_center_distance_px,
            pick=p,
            username_center=_last_username_center.get(key) if not is_username else None,
        )

    passed, verify_block = _run_verify(target, pick)
    parts.append(verify_block)

    if is_username and passed:
        try:
            ucx, ucy, _ = _node_center_distance(target, click_x, click_y)
            _last_username_center[key] = (ucx, ucy)
        except Exception:
            pass
    elif not is_username and passed:
        try:
            pcx, pcy, _ = _node_center_distance(target, click_x, click_y)
            _last_password_center[key] = (pcx, pcy)
        except Exception:
            pass

    if not passed and retry_on_verify_fail:
        time.sleep(0.2)
        if not is_username:
            target2, pick_retry, cx2, cy2 = _pick_credential_edit(
                device, x, y, field_label, username_center=_last_username_center.get(key)
            )
            if target2 is not None:
                target = target2
                click_x, click_y = cx2, cy2
                pick = pick_retry
                try:
                    target.click()
                    time.sleep(max(0.12, min(float(settle_s), 1.0)))
                except Exception:
                    device.click(click_x, click_y)
                    time.sleep(0.12)
        _clear_editable_field(target, device, aggressive=True)
        pick2, err2 = _apply_set_text(target, device, text, f"{pick}+retry")
        if err2:
            parts.append(err2)
        else:
            time.sleep(0.15)
            passed2, verify2 = _run_verify(target, pick2)
            parts.append("Retry fill after VERIFY fail.")
            parts.append(verify2)
            passed = passed2
            if is_username and passed:
                try:
                    ucx, ucy, _ = _node_center_distance(target, click_x, click_y)
                    _last_username_center[key] = (ucx, ucy)
                except Exception:
                    pass
            elif not is_username and passed:
                try:
                    pcx, pcy, _ = _node_center_distance(target, click_x, click_y)
                    _last_password_center[key] = (pcx, pcy)
                except Exception:
                    pass

    if not passed and "FAILED" in parts[-1]:
        parts.append(
            "Action: re-OCR login screen → confirm field centers → fill again "
            "or verify_credential_field."
        )

    return "\n".join(parts)


def fill_password_via_accessibility(
    serial: str | None,
    password: str,
    *,
    width: int,
    height: int,
    settle_s: float = 0.25,
    verify_after_fill: bool = False,
    max_center_distance_px: float = 150.0,
) -> str:
    """无密码 OCR 坐标时：用 u2 选账号框下方第二个 EditText 填密码。"""
    device = _connect_u2(serial)
    key = serial or "__default__"
    username_center = _last_username_center.get(key)
    hint_x = width // 2
    hint_y = height // 3
    if username_center is not None:
        hint_x, hint_y = username_center[0], username_center[1] + _MIN_PASSWORD_BELOW_USERNAME_PX

    target, pick, click_x, click_y = _pick_credential_edit(
        device,
        hint_x,
        hint_y,
        "password",
        username_center=username_center,
    )
    if target is None:
        return (
            "Accessibility fill failed: no password EditText below username. "
            "Fill username first or run: python -m uiautomator2 init"
        )

    try:
        target.click()
    except Exception:
        device.click(click_x, click_y)
    time.sleep(max(0.12, min(float(settle_s), 1.0)))

    pick, err = _apply_set_text(target, device, password, pick)
    if err:
        return err

    parts = [
        f"Filled password via accessibility (uiautomator2, {pick}) "
        f"click=({click_x},{click_y}). Secure keyboard OK.",
    ]
    if verify_after_fill:
        passed, verify_block = verify_credential_node(
            target,
            password,
            field_label="password",
            tap_x=click_x,
            tap_y=click_y,
            max_center_distance_px=max_center_distance_px,
            pick=pick,
            username_center=username_center,
        )
        parts.append(verify_block)
        if not passed:
            return "\n".join(parts)
    try:
        pcx, pcy, _ = _node_center_distance(target, click_x, click_y)
        _last_password_center[key] = (pcx, pcy)
    except Exception:
        pass
    return "\n".join(parts)


from game_agent.services.login_form_ocr import is_compound_login_label, is_standalone_login_label

_LOGIN_SEARCH_HINTS = ("登录", "Login", "LOG IN", "Sign in", "SIGN IN")


def _node_label_text(info: dict[str, Any]) -> str:
    parts = [
        str(info.get("text") or "").strip(),
        str(info.get("contentDescription") or "").strip(),
    ]
    return " ".join(p for p in parts if p).strip()


def _score_primary_login_button(
    text: str, cy: int, height: int, *, password_y: int | None = None
) -> float:
    """分数越高越像主 Login/登录 按钮（相对密码框位置，非写死屏高比例）。"""
    if is_compound_login_label(text):
        return -1.0
    t = text.strip()
    score = 500.0 if is_standalone_login_label(t) else 100.0
    score -= len(t) * 3.0
    h = max(1, int(height))
    if password_y is not None:
        dy = cy - password_y
        if dy < 80:
            score -= 400.0
        elif 120 <= dy <= 950:
            score += 250.0
        elif dy > 1100:
            score -= 150.0
    if cy > int(h * 0.92):
        score -= 400.0
    return score


def _collect_login_button_candidates(
    device: Any, *, min_y: int, height: int, password_y: int | None = None
) -> list[tuple[Any, str, int, int, float]]:
    seen: set[tuple[int, int, int, int]] = set()
    out: list[tuple[Any, str, int, int, float]] = []

    for hint in _LOGIN_SEARCH_HINTS:
        try:
            nodes = device(textContains=hint)
        except Exception:
            continue
        try:
            count = nodes.count
        except Exception:
            count = 0
        for i in range(count):
            try:
                el = nodes[i]
                if not el.exists:
                    continue
                info = el.info or {}
                if not info.get("visible", True):
                    continue
                bounds = info.get("bounds") or {}
                if not bounds:
                    continue
                key = (
                    int(bounds.get("left", 0)),
                    int(bounds.get("top", 0)),
                    int(bounds.get("right", 0)),
                    int(bounds.get("bottom", 0)),
                )
                if key in seen:
                    continue
                seen.add(key)
                label = _node_label_text(info)
                if not label or hint.lower() not in label.lower():
                    continue
                cx, cy = _bounds_center(bounds)
                if cy < min_y:
                    continue
                sc = _score_primary_login_button(
                    label, cy, height, password_y=password_y
                )
                if sc < 0:
                    continue
                out.append((el, label, cx, cy, sc))
            except Exception:
                continue
    return out


def _try_u2_login_tap(
    device: Any,
    *,
    height: int,
    password_y: int | None,
    settle_s: float,
) -> str | None:
    h = max(1, int(height))
    min_y = int(h * 0.45)
    candidates = _collect_login_button_candidates(
        device, min_y=min_y, height=h, password_y=password_y
    )
    if not candidates:
        return None
    el, label, cx, cy, sc = max(candidates, key=lambda t: t[4])
    try:
        el.click()
        time.sleep(max(0.15, min(float(settle_s), 1.5)))
        return (
            f"Login via u2 text='{label[:40]}' center=({cx},{cy}) score={sc:.0f}"
        )
    except Exception as e:
        logger.debug("u2 login click: %s", e)
        return None


def _press_enter_submit(device: Any, settle_s: float) -> str:
    parts: list[str] = []
    try:
        device.press("enter")
        parts.append("u2 ENTER")
    except Exception as e:
        parts.append(f"u2 ENTER: {e!s}")
    try:
        device.shell("input keyevent 66")
        parts.append("KEYCODE_ENTER")
    except Exception as e:
        parts.append(f"keyevent 66: {e!s}")
    time.sleep(max(0.2, min(float(settle_s), 1.5)))
    return "; ".join(parts)


def _hide_soft_keyboard(device: Any) -> str:
    for cmd in ("cmd input_method hide",):
        try:
            device.shell(cmd)
            time.sleep(0.15)
            return f"IME hide ok ({cmd})"
        except Exception as e:
            return f"IME hide failed: {e!s}"
    return "IME hide: skipped"


def submit_login_after_password(
    serial: str | None,
    adb: Any,
    *,
    width: int,
    height: int,
    cached_login_xy: tuple[int, int] | None,
    password_y: int | None,
    artifact_root: Path | None,
    screen_height: int,
    settle_s: float = 0.35,
    press_enter: bool = True,
    use_cached_coords: bool = True,
    try_dismiss: bool = True,
    press_back_on_dismiss: bool = False,
) -> str:
    """
    密码已填入后的登录提交（atomic_login 内部使用）。

    顺序：ENTER → 无障碍点 Login → 键盘前缓存的 Login 坐标 → 收键盘/藏 IME 后再试。
    安全键盘下截屏发黑属正常，不在收键盘后再 OCR。
    """
    device = _connect_u2(serial)
    wait = max(0.15, min(float(settle_s), 1.5))
    steps: list[str] = []

    def _tap_xy(xy: tuple[int, int], label: str) -> None:
        x, y = xy
        device.click(int(x), int(y))
        time.sleep(wait)
        steps.append(f"{label} tap ({x},{y})")

    if press_enter:
        steps.append(f"[1] {_press_enter_submit(device, settle_s)}")

    u2_first = _try_u2_login_tap(
        device, height=height, password_y=password_y, settle_s=settle_s
    )
    if u2_first:
        steps.append(f"[2] {u2_first}")
        return "\n".join(steps)

    if use_cached_coords and cached_login_xy is not None:
        _tap_xy(cached_login_xy, "[3] cached Login (pre-keyboard OCR)")
        u2_after = _try_u2_login_tap(
            device, height=height, password_y=password_y, settle_s=settle_s
        )
        if u2_after:
            steps.append(f"[3b] {u2_after}")
        steps.append(
            "[3] Submitted via cached coords (no post-dismiss OCR). "
            "Wait for atomic_login OCR verify or check_in_game if UI unchanged."
        )
        return "\n".join(steps)

    if try_dismiss:
        steps.append(
            f"[4] {dismiss_secure_keyboard_focus(serial, width=width, height=height, settle_s=settle_s, press_back=press_back_on_dismiss)}"
        )
        steps.append(f"[4b] {_hide_soft_keyboard(device)}")
        u2_second = _try_u2_login_tap(
            device, height=height, password_y=password_y, settle_s=settle_s
        )
        if u2_second:
            steps.append(f"[4c] {u2_second}")
            return "\n".join(steps)

    steps.append(
        "Login submit exhausted. Ensure use_cached_login_button_xy=true and "
        "login_submit_press_enter=true in settings executor section."
    )
    return "\n".join(steps)


def tap_submit_login_button(
    serial: str | None,
    *,
    width: int,
    height: int,
    ocr_login_x: int | None = None,
    ocr_login_y: int | None = None,
    password_y: int | None = None,
    settle_s: float = 0.35,
) -> str:
    """单步点 Login：优先 u2，可选 OCR 坐标兜底。"""
    device = _connect_u2(serial)
    u2_msg = _try_u2_login_tap(
        device, height=height, password_y=password_y, settle_s=settle_s
    )
    if u2_msg:
        return u2_msg
    if ocr_login_x is not None and ocr_login_y is not None:
        device.click(int(ocr_login_x), int(ocr_login_y))
        time.sleep(max(0.15, min(float(settle_s), 1.5)))
        return f"Login tap OCR ({ocr_login_x},{ocr_login_y})"
    return "Login tap failed: no u2 node and no OCR coords"


def dismiss_keyboard_tap_point(width: int, height: int) -> tuple[int, int]:
    """
    根据设备分辨率计算右上角空白点击坐标（距右/上各约 3% 屏宽/高，至少 16px）。
    """
    w = max(1, int(width))
    h = max(1, int(height))
    margin_x = max(16, int(w * 0.03))
    margin_y = max(16, int(h * 0.03))
    x = max(8, w - margin_x)
    y = max(8, margin_y)
    return x, y


def dismiss_secure_keyboard_focus(
    serial: str | None,
    *,
    width: int,
    height: int,
    settle_s: float = 0.35,
    press_back: bool = False,
) -> str:
    """
    填完密码后收起安全键盘：点击屏幕右上角空白区，可选再按 BACK。
    避免焦点停在密码框导致截屏全黑、无法 OCR。
    """
    x, y = dismiss_keyboard_tap_point(width, height)
    parts: list[str] = []

    try:
        device = _connect_u2(serial)
        device.click(x, y)
        parts.append(f"Dismiss tap ({x},{y}) via uiautomator2")
    except Exception as e:
        parts.append(f"Dismiss tap failed: {e!s}")

    time.sleep(max(0.15, min(float(settle_s), 1.5)))

    if press_back:
        try:
            device = _connect_u2(serial)
            device.press("back")
            parts.append("Pressed BACK")
            time.sleep(0.2)
        except Exception as e:
            parts.append(f"BACK failed: {e!s}")

    parts.append("Re-OCR login screen then tap Login button.")
    return "; ".join(parts)
