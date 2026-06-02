from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_PACKAGE_RE = re.compile(r"^[a-zA-Z][\w]*(?:\.[a-zA-Z][\w]*)+$")


class AdbService:
    """设备侧操作封装（对标 AppAgent 的 AndroidController，偏 Windows + exec-out）。"""

    def __init__(self, serial: str | None) -> None:
        self._serial = serial.strip() if serial else None

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
            raise RuntimeError(f"adb pull 失败: {remote!r} -> {local}: {err}")

    def uninstall(self, package: str, *, timeout: float = 60.0) -> str:
        pkg = (package or "").strip()
        if not pkg:
            return "拒绝：包名为空"
        r = self._run(["uninstall", pkg], timeout=timeout)
        if r.returncode == 0:
            return f"已卸载: {pkg}"
        return f"卸载返回码 {r.returncode}: {(r.stderr or r.stdout or '').strip()}"

    def _run(
        self,
        args: list[str],
        *,
        timeout: float = 120.0,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        cmd = self._base() + args
        logger.debug("adb %s", " ".join(cmd))
        return subprocess.run(
            cmd,
            capture_output=True,
            text=text,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
            timeout=timeout,
            check=False,
        )

    def verify_connection(self) -> str:
        r = self._run(["get-state"], timeout=15.0)
        if r.returncode != 0:
            return f"adb get-state 失败: {r.stderr.strip() or r.stdout.strip()}"
        state = (r.stdout or "").strip()
        if state != "device":
            return f"设备状态异常: {state!r}（期望 device）"
        return f"adb 已连接: state={state}"

    def shell(self, command: str, *, timeout: float = 60.0) -> str:
        r = self._run(["shell", command], timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"adb shell 失败: {command!r} err={r.stderr.strip()}")
        return r.stdout or ""

    def launch_game(self, package: str, activity: str | None) -> str:
        if activity:
            # activity 可为完整组件串 pkg/.Main 或 pkg/pkg.MainActivity（am start -n 单参数）
            cmd = f"am start -n {activity}"
        else:
            cmd = f"monkey -p {package} -c android.intent.category.LAUNCHER 1"
        out = self.shell(cmd, timeout=60.0)
        return f"已执行启动: {cmd}\n输出摘要: {out[:500]}"

    def force_stop_package(self, package: str) -> str:
        """结束指定包名应用进程（am force-stop，等同从最近任务划掉）。"""
        pkg = (package or "").strip()
        if not pkg:
            return "拒绝：包名为空"
        if not _PACKAGE_RE.match(pkg):
            return f"拒绝：非法包名 {package!r}"
        try:
            self.shell(f"am force-stop {pkg}", timeout=15.0)
            return f"已 force-stop: {pkg}"
        except subprocess.TimeoutExpired as e:
            return f"force-stop 超时: {pkg} timeout={e.timeout}s"
        except Exception as e:
            return f"force-stop 失败: {pkg} err={e!s}"

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
            return "未提供有效包名"
        return "\n".join(self.force_stop_package(p) for p in ordered)

    def screencap_png(self, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = self._run(["exec-out", "screencap", "-p"], timeout=45.0, text=False)
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="replace") if r.stderr else ""
            raise RuntimeError(f"screencap 失败: {err}")
        dest.write_bytes(r.stdout or b"")
        return dest

    def tap(self, x: int, y: int, *, width: int, height: int) -> str:
        if not (0 <= x < width and 0 <= y < height):
            return f"拒绝点击: ({x},{y}) 超出分辨率 {width}x{height}"
        try:
            self.shell(f"input tap {x} {y}", timeout=15.0)
            return f"已点击 ({x},{y})"
        except subprocess.TimeoutExpired as e:
            return f"点击超时: ({x},{y}) timeout={e.timeout}s"
        except Exception as e:
            return f"点击失败: ({x},{y}) err={e!s}"

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 400,
    ) -> str:
        try:
            self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}", timeout=20.0)
            return f"已滑动 ({x1},{y1})->({x2},{y2})"
        except subprocess.TimeoutExpired as e:
            return f"滑动超时: ({x1},{y1})->({x2},{y2}) timeout={e.timeout}s"
        except Exception as e:
            return f"滑动失败: ({x1},{y1})->({x2},{y2}) err={e!s}"

    def press_back(self) -> str:
        try:
            self.shell("input keyevent KEYCODE_BACK", timeout=10.0)
            return "已按返回键"
        except subprocess.TimeoutExpired as e:
            return f"返回键超时: timeout={e.timeout}s"
        except Exception as e:
            return f"返回键失败: {e!s}"

    def input_text_adb(self, text: str) -> str:
        """与 AppAgent 类似：空格替换为 %s，并去掉单引号。"""
        t = text.replace(" ", "%s").replace("'", "")
        try:
            self.shell(f"input text {t}", timeout=20.0)
            return "已通过 adb input text 输入（请确认焦点在正确输入框）"
        except subprocess.TimeoutExpired as e:
            return f"输入超时: timeout={e.timeout}s（请检查设备是否卡死）"
        except Exception as e:
            return f"输入失败: {e!s}"

    def wm_size(self) -> tuple[int, int]:
        out = self.shell("wm size", timeout=10.0)
        # Physical size: 1080x2400
        for part in out.replace("\n", " ").split():
            if "x" in part and part[0].isdigit():
                w, _, h = part.partition("x")
                if w.isdigit() and h.isdigit():
                    return int(w), int(h)
        return 1080, 1920

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
        logger.info("foreground 解析失败：所有 dumpsys 方案均未提取到组件")
        return None, None

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

    def wait_seconds(self, seconds: float) -> str:
        time.sleep(max(0.0, min(seconds, 60.0)))
        return f"已等待 {seconds:.1f}s"
