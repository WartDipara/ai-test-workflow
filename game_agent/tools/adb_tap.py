"""命令行工具：在已连接设备上执行 adb input tap。"""

from __future__ import annotations

import argparse
import sys

from game_agent.services.adb_service import AdbService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="在 Android 设备指定坐标执行 adb input tap",
    )
    parser.add_argument("x", type=int, help="点击 X 坐标（像素）")
    parser.add_argument("y", type=int, help="点击 Y 坐标（像素）")
    parser.add_argument(
        "-s",
        "--serial",
        default=None,
        help="adb 设备序列号（省略则使用默认设备）",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="执行前检查 adb 连接状态",
    )
    args = parser.parse_args(argv)

    adb = AdbService(args.serial)
    if args.verify:
        state = adb.verify_connection()
        if "失败" in state or "异常" in state:
            print(state, file=sys.stderr)
            return 1
        print(state)

    w, h = adb.wm_size()
    msg = adb.tap(args.x, args.y, width=w, height=h)
    print(msg)
    if msg.startswith("拒绝") or "失败" in msg or "超时" in msg:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
