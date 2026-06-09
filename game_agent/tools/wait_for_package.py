"""
等待设备上出现配置中的游戏包名（deploy 安装可能有延迟），可选检测到后 am start。

用法（仓库根目录）:
  python -m game_agent.tools.wait_for_package
  python -m game_agent.tools.wait_for_package --launch
  python -m game_agent.tools.wait_for_package --package com.foo.game --timeout 180
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from game_agent.config.loader import load_app_config
from game_agent.paths import REPO_ROOT
from game_agent.services.adb_service import AdbService
from game_agent.services.package_install import wait_for_package_installed

_DEFAULT_CONFIG = REPO_ROOT / "config" / "settings.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Poll adb until the game package is installed on the device (post-deploy delay).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"settings.yaml path (default: {_DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--package",
        required=True,
        help="目标游戏包名（由 APK 解析，不再从 settings.yaml 读取）",
    )
    parser.add_argument(
        "-s",
        "--serial",
        default=None,
        help="adb device serial (default device if omitted)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Max wait seconds (default: game.package_install_wait_timeout_s)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Poll interval seconds (default: game.package_install_poll_interval_s)",
    )
    parser.add_argument(
        "--launch",
        action="store_true",
        help="Run am start after package is detected",
    )
    parser.add_argument(
        "--verify-adb",
        action="store_true",
        help="Check adb connection before polling",
    )
    args = parser.parse_args(argv)

    config_path = args.config.resolve()
    if not config_path.is_file():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    cfg = load_app_config(config_path)
    pkg = args.package.strip()

    timeout_s = (
        args.timeout
        if args.timeout is not None
        else cfg.game.package_install_wait_timeout_s
    )
    interval_s = (
        args.interval
        if args.interval is not None
        else cfg.game.package_install_poll_interval_s
    )

    adb = AdbService(cfg.adb.serial if args.serial is None else args.serial)
    if args.verify_adb:
        state = adb.verify_connection()
        print(state)
        if "failed" in state.lower() or "Bad device" in state:
            return 1

    print(f"Waiting for package: {pkg} (timeout={timeout_s}s, interval={interval_s}s)")
    result = wait_for_package_installed(
        adb,
        pkg,
        timeout_s=timeout_s,
        poll_interval_s=interval_s,
    )
    print(result.to_tool_message())
    if not result.ok:
        return 1

    if args.launch:
        activity = cfg.game.launch_activity.strip()
        if not activity:
            print("game.launch_activity is empty; cannot --launch", file=sys.stderr)
            return 1
        msg = adb.launch_game(pkg, activity)
        print(msg)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
