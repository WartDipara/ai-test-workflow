from __future__ import annotations

import logging
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

_DEVICE_LINE_RE = re.compile(r"^(\S+)\s+device\s*$")


def _find_adb() -> str:
    found = shutil.which("adb")
    if found:
        return found
    return "adb"


def list_connected_devices(*, timeout_s: float = 15.0) -> list[str]:
    """
    解析 ``adb devices``，仅返回 state=device 的 serial 列表。
    """
    adb = _find_adb()
    try:
        proc = subprocess.run(
            [adb, "devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"adb devices failed: {exc}") from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(
            f"adb devices 退出码 {proc.returncode}"
            + (f": {stderr[:300]}" if stderr else ""),
        )

    serials: list[str] = []
    for line in (proc.stdout or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("list of devices"):
            continue
        match = _DEVICE_LINE_RE.match(stripped)
        if match:
            serials.append(match.group(1))

    logger.info("adb devices (%d): %s", len(serials), ", ".join(serials) or "(none)")
    return serials
