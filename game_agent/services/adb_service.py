from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_SERIAL_LOCKS: dict[str, threading.Lock] = {}
_SERIAL_LOCKS_GUARD = threading.Lock()
_TOUCH_SIZE_CACHE_TTL_S = 60.0
_ADB_TIMEOUT_RETRIES = 2


def _lock_for_serial(serial: str | None) -> threading.Lock:
    key = (serial or "").strip() or "__default__"
    with _SERIAL_LOCKS_GUARD:
        lock = _SERIAL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SERIAL_LOCKS[key] = lock
        return lock

_PACKAGE_RE = re.compile(r"^[a-zA-Z][\w]*(?:\.[a-zA-Z][\w]*)+$")


class AdbService:
    """设备侧操作封装（对标 AppAgent 的 AndroidController，偏 Windows + exec-out）。"""

    def __init__(self, serial: str | None) -> None:
        self._serial = serial.strip() if serial else None
        self._touch_size_cache: tuple[int, int] | None = None
        self._touch_size_cache_ts: float = 0.0

    @property
    def device_serial(self) -> str | None:
        return self._serial

    def _base(self) -> list[str]:
        if self._serial:
            return ["adb", "-s", self._serial]
        return ["adb"]

    def _serial_args(self) -> list[str]:
        """``adb`` 之后的序列号参数；无 serial 时为空列表。"""
        if self._serial:
            return ["-s", self._serial]
        return []

    def pull(self, remote: str, local: Path, *, timeout: float = 120.0) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        r = self._run(["pull", remote, str(local)], timeout=timeout)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            raise RuntimeError(f"adb pull failed: {remote!r} -> {local}: {err}")

    def uninstall(self, package: str, *, timeout: float = 60.0) -> str:
        pkg = (package or "").strip()
        if not pkg:
            return "Refused: empty package name"
        r = self._run(["uninstall", pkg], timeout=timeout)
        if r.returncode == 0:
            return f"Uninstalled: {pkg}"
        return f"Uninstall exit {r.returncode}: {(r.stderr or r.stdout or '').strip()}"

    def _device_lock(self) -> threading.Lock:
        return _lock_for_serial(self._serial)

    def _run(
        self,
        args: list[str],
        *,
        timeout: float = 120.0,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        cmd = self._base() + args
        logger.debug("adb %s", " ".join(cmd))
        attempts = _ADB_TIMEOUT_RETRIES + 1
        for attempt in range(attempts):
            try:
                with self._device_lock():
                    return subprocess.run(
                        cmd,
                        capture_output=True,
                        text=text,
                        encoding="utf-8" if text else None,
                        errors="replace" if text else None,
                        timeout=timeout,
                        check=False,
                    )
            except subprocess.TimeoutExpired:
                if attempt + 1 >= attempts:
                    raise
                logger.warning(
                    "adb 超时，重试 %d/%d serial=%s cmd=%s",
                    attempt + 1,
                    attempts,
                    self._serial or "-",
                    " ".join(cmd),
                )
                time.sleep(0.35 * (attempt + 1))
        raise RuntimeError(f"adb command failed: {' '.join(cmd)}")

    def verify_connection(self) -> str:
        r = self._run(["get-state"], timeout=15.0)
        if r.returncode != 0:
            return f"adb get-state failed: {r.stderr.strip() or r.stdout.strip()}"
        state = (r.stdout or "").strip()
        if state != "device":
            return f"Bad device state: {state!r} (expected device)"
        return f"adb connected: state={state}"

    def shell(self, command: str, *, timeout: float = 60.0) -> str:
        r = self._run(["shell", command], timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"adb shell failed: {command!r} err={r.stderr.strip()}")
        return r.stdout or ""

    def launch_game(self, package: str, activity: str | None) -> str:
        if activity:
            # activity 可为完整组件串 pkg/.Main 或 pkg/pkg.MainActivity（am start -n 单参数）
            cmd = f"am start -n {activity}"
        else:
            cmd = f"monkey -p {package} -c android.intent.category.LAUNCHER 1"
        out = self.shell(cmd, timeout=60.0)
        return f"Launched: {cmd}\nOutput: {out[:500]}"

    def force_stop_package(self, package: str) -> str:
        """结束指定包名应用进程（am force-stop，等同从最近任务划掉）。"""
        pkg = (package or "").strip()
        if not pkg:
            return "Refused: empty package name"
        if not _PACKAGE_RE.match(pkg):
            return f"Refused: invalid package {package!r}"
        try:
            self.shell(f"am force-stop {pkg}", timeout=15.0)
            return f"force-stop OK: {pkg}"
        except subprocess.TimeoutExpired as e:
            return f"force-stop timeout: {pkg} timeout={e.timeout}s"
        except Exception as e:
            return f"force-stop failed: {pkg} err={e!s}"

    def force_stop_packages(self, packages: list[str]) -> str:
        """批量 force-stop；去重并保持顺序。"""
        seen: set[str] = set()
        ordered: list[str] = []
        for raw in packages:
            pkg = (raw or "").strip()
            if not pkg or pkg in seen:
                continue
            seen.add(pkg)
            ordered.append(pkg)
        if not ordered:
            return "No valid package names provided"
        return "\n".join(self.force_stop_package(p) for p in ordered)

    def screencap_png(self, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = self._run(["exec-out", "screencap", "-p"], timeout=45.0, text=False)
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="replace") if r.stderr else ""
            raise RuntimeError(f"screencap failed: {err}")
        dest.write_bytes(r.stdout or b"")
        return dest

    def tap(self, x: int, y: int, *, width: int, height: int) -> str:
        if not (0 <= x < width and 0 <= y < height):
            return f"Refused tap: ({x},{y}) outside {width}x{height}"
        try:
            self.shell(f"input tap {x} {y}", timeout=15.0)
            return f"Tapped ({x},{y})"
        except subprocess.TimeoutExpired as e:
            return f"Tap timeout: ({x},{y}) timeout={e.timeout}s"
        except Exception as e:
            return f"Tap failed: ({x},{y}) err={e!s}"

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 400,
        *,
        width: int = 0,
        height: int = 0,
    ) -> str:
        if width > 0 and height > 0:
            if not (0 <= x1 < width and 0 <= y1 < height):
                return f"Refused swipe: start ({x1},{y1}) outside {width}x{height}"
            if not (0 <= x2 < width and 0 <= y2 < height):
                return f"Refused swipe: end ({x2},{y2}) outside {width}x{height}"
        try:
            self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}", timeout=20.0)
            return f"Swiped ({x1},{y1})->({x2},{y2})"
        except subprocess.TimeoutExpired as e:
            return f"Swipe timeout: ({x1},{y1})->({x2},{y2}) timeout={e.timeout}s"
        except Exception as e:
            return f"Swipe failed: ({x1},{y1})->({x2},{y2}) err={e!s}"

    def press_back(self) -> str:
        try:
            self.shell("input keyevent KEYCODE_BACK", timeout=10.0)
            return "Pressed BACK"
        except subprocess.TimeoutExpired as e:
            return f"BACK timeout: timeout={e.timeout}s"
        except Exception as e:
            return f"BACK failed: {e!s}"

    def input_text_adb(self, text: str) -> str:
        """与 AppAgent 类似：空格替换为 %s，并去掉单引号（仅适合 ASCII）。"""
        t = text.replace(" ", "%s").replace("'", "")
        try:
            self.shell(f"input text {t}", timeout=20.0)
            return "Typed via adb input text (ensure correct field focused)"
        except subprocess.TimeoutExpired as e:
            return f"Input timeout: timeout={e.timeout}s (device stuck?)"
        except Exception as e:
            return f"Input failed: {e!s}"

    def clear_focused_text(self, *, delete_rounds: int = 40) -> str:
        """假定输入框已获焦点：先移到文首/文尾再批量删除，尽量清空已有内容。"""
        delete_rounds = max(8, min(int(delete_rounds), 80))
        fwd = "; ".join(["input keyevent 112"] * delete_rounds)
        bwd = "; ".join(["input keyevent 67"] * delete_rounds)
        script = f"input keyevent 122; {fwd}; input keyevent 123; {bwd}"
        try:
            self.shell(script, timeout=45.0)
            return f"Cleared focused field (~{delete_rounds * 2} delete keyevents)"
        except subprocess.TimeoutExpired as e:
            return f"Clear field timeout: timeout={e.timeout}s"
        except Exception as e:
            return f"Clear field may be incomplete: {e!s}"

    def paste_text(self, text: str) -> str:
        """经系统剪贴板粘贴（支持中文与特殊字符）；失败时回退 input text。"""
        r = self._run(["shell", "cmd", "clipboard", "set-text", text], timeout=15.0)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            logger.warning("clipboard set-text failed, fallback input text: %s", err[:200])
            return self.input_text_adb(text)
        try:
            self.shell("input keyevent 279", timeout=10.0)
            return "Pasted via clipboard"
        except Exception as e:
            logger.warning("paste key failed, fallback input text: %s", e)
            return self.input_text_adb(text)

    def fill_text_at(
        self,
        x: int,
        y: int,
        text: str,
        *,
        width: int,
        height: int,
        settle_s: float = 0.35,
    ) -> str:
        """点击坐标 → 短暂等待 → 清空 → 填入文本。"""
        tap_msg = self.tap(x, y, width=width, height=height)
        if "Refused tap" in tap_msg:
            return tap_msg
        time.sleep(max(0.1, min(float(settle_s), 2.0)))
        clear_msg = self.clear_focused_text()
        type_msg = self.paste_text(text)
        return f"{tap_msg}\n{clear_msg}\n{type_msg}"

    def wm_size(self) -> tuple[int, int]:
        try:
            out = self.shell("wm size", timeout=10.0)
        except subprocess.TimeoutExpired:
            if self._touch_size_cache is not None:
                logger.warning(
                    "wm size 超时，使用缓存 %dx%d serial=%s",
                    self._touch_size_cache[0],
                    self._touch_size_cache[1],
                    self._serial or "-",
                )
                return self._touch_size_cache
            logger.warning(
                "wm size 超时，回退默认 1080x1920 serial=%s",
                self._serial or "-",
            )
            return 1080, 1920
        # Physical size: 1080x2400
        for part in out.replace("\n", " ").split():
            if "x" in part and part[0].isdigit():
                w, _, h = part.partition("x")
                if w.isdigit() and h.isdigit():
                    return int(w), int(h)
        return 1080, 1920

    def get_screen_rotation(self) -> int:
        """返回屏幕旋转角度: 0, 90, 180, 270。"""
        out = self.shell("dumpsys window", timeout=15.0)
        for line in out.splitlines():
            if "display=0" in line and "mRotation=" in line:
                for token in line.split():
                    if token.startswith("mRotation="):
                        try:
                            deg = int(token.split("=")[1].replace("ROTATION_", ""))
                            return deg
                        except (ValueError, IndexError):
                            pass
        return 0

    def touch_size(self, *, max_age_s: float = _TOUCH_SIZE_CACHE_TTL_S) -> tuple[int, int]:
        """返回当前方向下 adb input tap 的有效触控空间（含旋转补偿）。"""
        now = time.monotonic()
        if (
            self._touch_size_cache is not None
            and (now - self._touch_size_cache_ts) < max(1.0, max_age_s)
        ):
            return self._touch_size_cache
        w, h = self.wm_size()
        try:
            rot = self.get_screen_rotation()
        except subprocess.TimeoutExpired:
            logger.warning(
                "get_screen_rotation 超时，假定竖屏 serial=%s",
                self._serial or "-",
            )
            rot = 0
        if rot in (90, 270):
            w, h = h, w
        self._touch_size_cache = (w, h)
        self._touch_size_cache_ts = now
        return w, h

    def current_foreground_app(self) -> tuple[str | None, str | None]:
        """
        返回当前前台 (package, activity)；解析失败时为 (None, None)。
        兼容多系统输出：mCurrentFocus/mFocusedApp/topResumedActivity/mResumedActivity。
        """
        def extract_component(text: str, preferred_markers: tuple[str, ...]) -> tuple[str | None, str | None]:
            component_re = re.compile(
                r"([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/([A-Za-z0-9_.$]+)",
            )
            lines = text.splitlines()
            for marker in preferred_markers:
                for line in lines:
                    if marker not in line:
                        continue
                    m = component_re.search(line)
                    if not m:
                        continue
                    pkg, act = m.group(1), m.group(2)
                    if act.startswith("."):
                        act = f"{pkg}{act}"
                    return pkg, act
            for line in lines:
                m = component_re.search(line)
                if not m:
                    continue
                pkg, act = m.group(1), m.group(2)
                if act.startswith("."):
                    act = f"{pkg}{act}"
                return pkg, act
            return None, None

        sources = [
            ("dumpsys window windows", ("mCurrentFocus", "mFocusedApp")),
            ("dumpsys activity activities", ("topResumedActivity", "mResumedActivity", "ResumedActivity")),
            ("dumpsys activity top", ("topResumedActivity", "mResumedActivity", "ACTIVITY", "TASK")),
        ]
        for cmd, markers in sources:
            try:
                out = self.shell(cmd, timeout=15.0)
            except Exception:
                out = ""
            if not out:
                continue
            pkg, act = extract_component(out, markers)
            if pkg and act:
                return pkg, act
        logger.info("foreground parse failed: no component from dumpsys")
        return None, None

    def install_apk(self, apk_path: Path, *, timeout: float = 300.0) -> str:
        """安装本地 APK（adb install -r）。"""
        path = apk_path.resolve()
        if not path.is_file():
            return f"Refused install: APK not found: {path}"
        r = self._run(
            ["install", "-r", str(path)],
            timeout=timeout,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        combined = "\n".join(x for x in (out, err) if x)
        if r.returncode != 0:
            return f"Install failed (exit {r.returncode}): {combined[:800]}"
        return f"Installed: {path.name}\n{combined[:400]}"

    def is_package_installed(self, package: str) -> bool:
        """设备上是否已安装指定包（pm path）。"""
        pkg = (package or "").strip()
        if not pkg or not _PACKAGE_RE.match(pkg):
            return False
        r = self._run(["shell", "pm", "path", pkg], timeout=20.0)
        if r.returncode != 0:
            return False
        out = (r.stdout or "").strip()
        return out.startswith("package:") or "package:" in out

    def is_package_running(self, package: str) -> bool:
        """设备上是否存在指定包名的运行中进程（用于判定游戏是否已启动）。"""
        pkg = (package or "").strip()
        if not pkg or not _PACKAGE_RE.match(pkg):
            return False
        r = self._run(["shell", "pidof", pkg], timeout=10.0)
        if r.returncode == 0 and (r.stdout or "").strip():
            return True
        r2 = self._run(["shell", "pgrep", "-f", pkg], timeout=10.0)
        return r2.returncode == 0 and bool((r2.stdout or "").strip())

    def wait_seconds(
        self,
        seconds: float,
        *,
        should_abort: Callable[[], bool] | None = None,
    ) -> str:
        total = max(0.0, min(seconds, 60.0))
        deadline = time.monotonic() + total
        while time.monotonic() < deadline:
            if should_abort and should_abort():
                waited = total - (deadline - time.monotonic())
                return f"Wait aborted after {max(0.0, waited):.1f}s"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.25, remaining))
        return f"Waited {seconds:.1f}s"
