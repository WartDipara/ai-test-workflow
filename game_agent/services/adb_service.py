from __future__ import annotations

import logging
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)


class AdbService:
    """设备侧操作封装（对标 AppAgent 的 AndroidController，偏 Windows + exec-out）。"""

    def __init__(self, serial: str | None) -> None:
        self._serial = serial.strip() if serial else None

    def _base(self) -> list[str]:
        if self._serial:
            return ["adb", "-s", self._serial]
        return ["adb"]

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
            cmd = f"am start -n {activity}"
        else:
            cmd = f"monkey -p {package} -c android.intent.category.LAUNCHER 1"
        out = self.shell(cmd, timeout=60.0)
        return f"已执行启动: {cmd}\n输出摘要: {out[:500]}"

    def screencap_png(self, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = self._run(["exec-out", "screencap", "-p"], timeout=45.0, text=False)
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="replace") if r.stderr else ""
            raise RuntimeError(f"screencap 失败: {err}")
        dest.write_bytes(r.stdout or b"")
        return dest

    def dump_ui_xml(self) -> str:
        remote = "/sdcard/window_dump.xml"
        r1 = self._run(["shell", "uiautomator", "dump", remote], timeout=30.0)
        if r1.returncode != 0:
            raise RuntimeError(f"uiautomator dump 失败: {r1.stderr.strip()}")
        r2 = self._run(["exec-out", "shell", "cat", remote], timeout=30.0)
        if r2.returncode != 0:
            raise RuntimeError(f"读取 dump 失败: {r2.stderr.strip()}")
        return r2.stdout or ""

    def summarize_clickable_elements(self, max_nodes: int = 40) -> str:
        try:
            xml_text = self.dump_ui_xml()
        except RuntimeError as e:
            return f"[dump 不可用] {e}"
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".xml",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(xml_text)
            tmp_path = tmp.name
        try:
            return _summarize_xml(tmp_path, max_nodes=max_nodes)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def tap(self, x: int, y: int, *, width: int, height: int) -> str:
        if not (0 <= x < width and 0 <= y < height):
            return f"拒绝点击: ({x},{y}) 超出分辨率 {width}x{height}"
        self.shell(f"input tap {x} {y}", timeout=15.0)
        return f"已点击 ({x},{y})"

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 400,
    ) -> str:
        self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}", timeout=20.0)
        return f"已滑动 ({x1},{y1})->({x2},{y2})"

    def press_back(self) -> str:
        self.shell("input keyevent KEYCODE_BACK", timeout=10.0)
        return "已按返回键"

    def input_text_adb(self, text: str) -> str:
        """与 AppAgent 类似：空格替换为 %s，并去掉单引号。"""
        t = text.replace(" ", "%s").replace("'", "")
        self.shell(f"input text {t}", timeout=20.0)
        return "已通过 adb input text 输入（请确认焦点在正确输入框）"

    def logcat_tail(self, lines: int = 120) -> str:
        r = self._run(["logcat", "-d", "-t", str(lines)], timeout=30.0)
        out = (r.stdout or "") + (r.stderr or "")
        return out[-12000:] if len(out) > 12000 else out

    def wm_size(self) -> tuple[int, int]:
        out = self.shell("wm size", timeout=10.0)
        # Physical size: 1080x2400
        for part in out.replace("\n", " ").split():
            if "x" in part and part[0].isdigit():
                w, _, h = part.partition("x")
                if w.isdigit() and h.isdigit():
                    return int(w), int(h)
        return 1080, 1920

    def wait_seconds(self, seconds: float) -> str:
        time.sleep(max(0.0, min(seconds, 60.0)))
        return f"已等待 {seconds:.1f}s"


def _summarize_xml(xml_path: str, *, max_nodes: int) -> str:
    lines: list[str] = []
    try:
        for _event, elem in ET.iterparse(xml_path, events=("end",)):
            if elem.tag != "node":
                continue
            clickable = elem.attrib.get("clickable") == "true"
            if not clickable:
                continue
            bounds = elem.attrib.get("bounds", "")
            rid = elem.attrib.get("resource-id", "")
            text = elem.attrib.get("text", "")
            desc = elem.attrib.get("content-desc", "")
            center = _bounds_center(bounds)
            if center is None:
                continue
            cx, cy = center
            label = text or desc or rid or "(no text)"
            short = label.replace("\n", " ")[:80]
            lines.append(f"- ({cx},{cy}) id={rid!r} text/desc={short!r}")
            elem.clear()
            if len(lines) >= max_nodes:
                break
    except ET.ParseError as e:
        return f"[XML 解析失败] {e}"
    if not lines:
        return "[无 clickable=true 节点摘要；可能为 SurfaceView/全屏游戏界面]"
    return "\n".join(lines)


def _bounds_center(bounds: str) -> tuple[int, int] | None:
    # [0,0][1080,100]
    try:
        b = bounds.replace("][", ",").replace("[", "").replace("]", "")
        parts = [p.strip() for p in b.split(",") if p.strip()]
        if len(parts) != 4:
            return None
        x1, y1, x2, y2 = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
        return (x1 + x2) // 2, (y1 + y2) // 2
    except (ValueError, IndexError):
        return None
