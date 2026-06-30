from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_PACKAGE_RE = re.compile(r"package:\s*name='([^']+)'")
_LAUNCHABLE_ACTIVITY_RE = re.compile(r"launchable-activity:\s*name='([^']+)'")


@dataclass(frozen=True)
class ApkLaunchInfo:
    """从 APK badging 提取的启动信息。"""

    package_name: str
    launch_activity: str


def _find_aapt_executable() -> str:
    import os
    import shutil

    found = shutil.which("aapt")
    if found:
        return found

    sdk_root = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if sdk_root:
        build_tools = Path(sdk_root) / "build-tools"
        if build_tools.is_dir():
            versions = sorted(build_tools.iterdir(), key=lambda p: p.name, reverse=True)
            for ver in versions:
                for name in ("aapt.exe", "aapt"):
                    candidate = ver / name
                    if candidate.is_file():
                        return str(candidate)
    return "aapt"


def _dump_apk_badging(apk_path: Path) -> str | None:
    if not apk_path.exists():
        logger.warning("APK file missing: %s", apk_path)
        return None
    aapt = _find_aapt_executable()
    try:
        result = subprocess.run(
            [aapt, "dump", "badging", str(apk_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return result.stdout
    except FileNotFoundError:
        logger.error("aapt not found (%s); install Android build-tools and add to PATH.", aapt)
        return None
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        logger.error("aapt failed: %s", stderr)
        return None


def get_apk_launch_info(apk_path: Path | str) -> ApkLaunchInfo | None:
    """从 APK 提取包名与 launchable-activity（用于 am start -n）。"""
    badging = _dump_apk_badging(Path(apk_path))
    if not badging:
        return None

    pkg_match = _PACKAGE_RE.search(badging)
    act_match = _LAUNCHABLE_ACTIVITY_RE.search(badging)
    if not pkg_match:
        logger.warning("package name not found in aapt output.")
        return None
    if not act_match:
        logger.warning("launchable-activity not found in aapt output.")
        return None

    return ApkLaunchInfo(
        package_name=pkg_match.group(1),
        launch_activity=act_match.group(1),
    )
