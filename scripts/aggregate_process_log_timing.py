#!/usr/bin/env python3
"""从 process.log 聚合 OCR / interpret / net_watch 等耗时，用于优化前后对比。"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path


_OCR_DONE_RE = re.compile(
    r"OCR 完成 (?P<sec>[\d.]+)s profile=(?P<profile>\S+) src=(?P<src>\S+)",
)
_INTERPRET_RE = re.compile(
    r"\[LaunchGraph:classify\] sync interpret",
)
_NET_WATCH_RE = re.compile(r"net_watch_\d+_\d+\.png")
_ANALYZE_RE = re.compile(r"\[LaunchGraph:recover\] analyze_screen")


def aggregate_log(path: Path) -> dict[str, float | int]:
    ocr_secs: list[float] = []
    ocr_by_src: dict[str, float] = defaultdict(float)
    interpret_count = 0
    net_watch_count = 0
    analyze_count = 0

    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        m = _OCR_DONE_RE.search(line)
        if m:
            sec = float(m.group("sec"))
            src = m.group("src")
            ocr_secs.append(sec)
            ocr_by_src[src] += sec
        if _INTERPRET_RE.search(line):
            interpret_count += 1
        if _NET_WATCH_RE.search(line):
            net_watch_count += 1
        if _ANALYZE_RE.search(line):
            analyze_count += 1

    duplicate_frames = sum(1 for total in ocr_by_src.values() if total > 0)
    multi_infer_frames = sum(1 for total in ocr_by_src.values() if total > 15.0)

    return {
        "ocr_calls": len(ocr_secs),
        "ocr_total_s": round(sum(ocr_secs), 2),
        "ocr_avg_s": round(sum(ocr_secs) / len(ocr_secs), 2) if ocr_secs else 0.0,
        "ocr_max_s": round(max(ocr_secs), 2) if ocr_secs else 0.0,
        "unique_screenshot_frames": len(ocr_by_src),
        "likely_multi_infer_frames": multi_infer_frames,
        "sync_interpret_count": interpret_count,
        "net_watch_screenshots": net_watch_count,
        "analyze_screen_count": analyze_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="聚合 process.log 性能指标")
    parser.add_argument("log_path", type=Path, help="process.log 路径")
    args = parser.parse_args(argv)

    if not args.log_path.is_file():
        print(f"文件不存在: {args.log_path}", file=sys.stderr)
        return 1

    stats = aggregate_log(args.log_path)
    print(f"=== {args.log_path} ===")
    for key, value in stats.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
