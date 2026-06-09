from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game_agent.models.settings import AppConfig

logger = logging.getLogger(__name__)

_PACKAGE_RE = re.compile(r"package:\s*name='([^']+)'")
_LAUNCHABLE_ACTIVITY_RE = re.compile(r"launchable-activity:\s*name='([^']+)'")


@dataclass(frozen=True)
class ApkLaunchInfo:
    """从 APK badging 提取的启动信息。"""

    package_name: str
    launch_activity: str  # 完整组件串，如 com.foo/.MainActivity 或 com.foo/com.foo.Main


def _find_aapt_executable() -> str:
    """优先 PATH 中的 aapt；Windows 下尝试 Android SDK build-tools。"""
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
        logger.warning("APK 文件不存在: %s", apk_path)
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
        logger.error("aapt 命令未找到 (%s)，请安装 Android build-tools 并加入 PATH。", aapt)
        return None
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        logger.error("aapt 执行失败: %s", stderr)
        return None


def get_apk_launch_info(apk_path: Path | str) -> ApkLaunchInfo | None:
    """
    从 APK 提取包名与 launchable-activity（用于 am start -n）。
    """
    badging = _dump_apk_badging(Path(apk_path))
    if not badging:
        return None

    pkg_match = _PACKAGE_RE.search(badging)
    act_match = _LAUNCHABLE_ACTIVITY_RE.search(badging)
    if not pkg_match:
        logger.warning("未能从 aapt 输出中找到 package name。")
        return None
    if not act_match:
        logger.warning("未能从 aapt 输出中找到 launchable-activity。")
        return None

    return ApkLaunchInfo(
        package_name=pkg_match.group(1),
        launch_activity=act_match.group(1),
    )


def _apply_game_fields_to_yaml_lines(
    lines: list[str],
    fields: dict[str, str],
) -> bool:
    """在 game: 块内更新 package_name / launch_activity 等字段。"""
    in_game = False
    game_indent = 2
    found: dict[str, bool] = {k: False for k in fields}
    changed = False
    game_end_idx: int | None = None

    for i, line in enumerate(lines):
        if line.startswith("game:"):
            in_game = True
            game_indent = 2
            continue

        if in_game and line.strip() and not line.startswith(" ") and not line.startswith("#"):
            game_end_idx = i
            in_game = False
            continue

        if not in_game:
            continue

        if line.startswith(" "):
            game_indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        for key in fields:
            if stripped.startswith(f"{key}:"):
                found[key] = True
                new_line = f"{' ' * game_indent}{key}: \"{fields[key]}\""
                if lines[i] != new_line:
                    lines[i] = new_line
                    changed = True

    missing = [k for k, ok in found.items() if not ok]
    if missing:
        insert_lines = [f"{' ' * game_indent}{k}: \"{fields[k]}\"" for k in missing]
        if game_end_idx is not None:
            lines[game_end_idx:game_end_idx] = insert_lines
        else:
            lines.extend(insert_lines)
        changed = True

    return changed


def apply_game_launch_info_to_config(cfg: AppConfig, apk_path: Path) -> AppConfig | None:
    """将 APK 解析出的 package/activity 写入内存中的 AppConfig（不写 YAML）。"""
    info = get_apk_launch_info(apk_path)
    if not info:
        return None
    return cfg.model_copy(
        update={
            "game": cfg.game.model_copy(
                update={
                    "package_name": info.package_name,
                    "launch_activity": info.launch_activity,
                },
            ),
        },
    )


def update_settings_yaml_from_apk(settings_path: Path, apk_path: Path) -> bool:
    """
    从 APK 提取 package_name、launch_activity，覆写 settings.yaml 的 game: 段。
    kill/uninstall 用 package_name；am start -n 用 launch_activity。
    """
    info = get_apk_launch_info(apk_path)
    if not info:
        return False

    try:
        lines = settings_path.read_text(encoding="utf-8").splitlines()
        changed = _apply_game_fields_to_yaml_lines(
            lines,
            {
                "package_name": info.package_name,
                "launch_activity": info.launch_activity,
            },
        )

        if changed:
            settings_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            logger.info(
                "已更新 %s game 段: package_name=%s launch_activity=%s",
                settings_path.name,
                info.package_name,
                info.launch_activity,
            )
        else:
            logger.info(
                "APK 信息与 %s 中 game 段一致，无需更新 (package=%s)",
                settings_path.name,
                info.package_name,
            )
        return True
    except Exception as e:
        logger.error("复写 %s 失败: %s", settings_path, e)
        return False
