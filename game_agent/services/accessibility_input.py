from __future__ import annotations

import logging
import math
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_device_lock = threading.Lock()
_devices: dict[str, Any] = {}

_last_username_center: dict[str, tuple[int, int]] = {}
_last_password_center: dict[str, tuple[int, int]] = {}

_EDIT_TEXT_CLASS_PATTERN = ".*(EditText|AutoCompleteTextView).*"

_MIN_PASSWORD_BELOW_USERNAME_PX = 48
_SAME_FIELD_CENTER_PX = 55


def get_last_password_center_y(serial: str | None) -> int | None:
    c = _last_password_center.get(serial or "__default__")
    return c[1] if c else None


def _connect_u2(serial: str | None) -> Any:
    try:
        import uiautomator2 as u2
    except ImportError as e:
        raise RuntimeError(
            "未安装 uiautomator2。请执行: pip install uiautomator2，"
            "并在设备上运行一次: python -m uiautomator2 init"
        ) from e

    key = serial or "__default__"
    with _device_lock:
        cached = _devices.get(key)
        if cached is not None:
            return cached
        logger.info("uiautomator2 连接设备 serial=%s", serial or "(default)")
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
                "after keyboard dismiss use get_ocr_summary or verify_credential_field again"
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
            f"VERIFY {field_label}: FAILED — fix coordinates with get_ocr_summary, then re-fill"
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
            "Fill username first; re-run get_ocr_summary for field centers."
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


def _clear_editable_field(target: Any, device: Any) -> None:
    """
    写入前清空输入框，避免重试或再次 fill 时叠在旧内容后面。
    顺序：clear_text → set_text('', clear=True) → 焦点上批量 DEL（兼容安全键盘）。
    """
    if hasattr(target, "clear_text"):
        try:
            target.clear_text()
            time.sleep(0.06)
        except Exception as e:
            logger.debug("clear_text: %s", e)

    if hasattr(target, "set_text"):
        try:
            target.set_text("")
            time.sleep(0.06)
        except Exception as e:
            logger.debug("set_text empty: %s", e)

    try:
        target.click()
        time.sleep(0.08)
    except Exception:
        pass

    try:
        device.shell("input keyevent 123")  # MOVE_END
        for _ in range(48):
            device.shell("input keyevent 67")  # DEL
        device.shell("input keyevent 122")  # MOVE_HOME
        for _ in range(48):
            device.shell("input keyevent 112")  # FORWARD_DEL
        time.sleep(0.06)
    except Exception as e:
        logger.debug("keyevent clear: %s", e)


def _set_text_replace(target: Any, text: str) -> None:
    """uiautomator2 部分版本不支持 set_text(..., clear=)；先清空再写入。"""
    if hasattr(target, "clear_text"):
        target.clear_text()
    elif hasattr(target, "set_text"):
        target.set_text("")
    if hasattr(target, "set_text"):
        target.set_text(text)


def _apply_set_text(target: Any, device: Any, text: str, pick: str) -> tuple[str, str | None]:
    _clear_editable_field(target, device)
    if _read_editable_text(target):
        _clear_editable_field(target, device)

    try:
        if hasattr(target, "set_text") or hasattr(target, "clear_text"):
            _set_text_replace(target, text)
            return pick, None
        _clear_editable_field(target, device)
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
        _clear_editable_field(target, device)
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
            "Action: get_ocr_summary → confirm field centers → fill_credential_field again "
            "or verify_credential_field."
        )

    return "\n".join(parts)


from game_agent.services.login_form_ocr import is_compound_login_label, is_standalone_login_label

_LOGIN_SEARCH_HINTS = ("登录", "Login", "LOG IN")


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
    ocr_after_dismiss: bool = False,
) -> str:
    """
    密码已填入后的登录提交（不依赖「收键盘 → OCR」单一路径）。

    顺序：ENTER → 无障碍点 Login → 键盘前缓存的 Login 坐标 → 收键盘/藏 IME 后再试
    →（可选）非黑屏时才 OCR。安全键盘下截屏发黑属正常，不代表游戏卡死。
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
            "Wait / check_in_game / get_ocr_summary if UI unchanged."
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

    if ocr_after_dismiss and artifact_root is not None and adb is not None:
        from game_agent.services.login_form_ocr import capture_login_form_targets
        from game_agent.utils.ocr_util import is_screencap_mostly_black

        targets, shot, summary = capture_login_form_targets(
            adb,
            artifact_root,
            screen_width=width,
            screen_height=screen_height,
            tag="login_post_dismiss",
        )
        steps.append(f"[5] {summary}")
        if is_screencap_mostly_black(shot):
            steps.append(
                "[5] OCR skipped: screencap mostly black (secure keyboard). "
                "Not game freeze — cache Login via get_ocr_summary before filling password."
            )
        elif targets.login_button_xy is not None:
            lx, ly = targets.login_button_xy
            _tap_xy((lx, ly), "[5] OCR Login")
            return "\n".join(steps)

    steps.append(
        "Login submit exhausted. Before password: call get_ocr_summary once (keyboard down) "
        "to cache Login; ensure login_submit_press_enter=true."
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

    parts.append("Re-run get_ocr_summary then tap_coordinate on Login.")
    return "; ".join(parts)
