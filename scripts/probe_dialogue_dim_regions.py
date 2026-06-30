#!/usr/bin/env python3
"""本地探针：对话暗色区域检测与标注。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from game_agent.services.dialogue_dim_locator import locate_dialogue_dim_regions
from game_agent.utils.ocr_util import run_ocr_frame


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe dialogue dim regions on a screenshot.")
    parser.add_argument("image", type=Path, help="Screenshot path")
    parser.add_argument(
        "--annotate",
        type=Path,
        default=None,
        help="Output annotated image path",
    )
    parser.add_argument("--no-ocr", action="store_true", help="Skip OCR bbox exclusion")
    args = parser.parse_args()

    if not args.image.is_file():
        print(f"image not found: {args.image}", file=sys.stderr)
        return 1

    bboxes = []
    if not args.no_ocr:
        ocr_summary, bboxes = run_ocr_frame(args.image, device_w=1080, device_h=2400)
        print(f"OCR lines: {len(ocr_summary.splitlines())} bboxes: {len(bboxes)}")

    out_dir = args.annotate.parent if args.annotate else args.image.parent
    out_name = args.annotate.name if args.annotate else f"dim_probe_{args.image.stem}.png"

    result = locate_dialogue_dim_regions(
        args.image,
        bboxes=bboxes,
        artifact_root=out_dir,
        annotate_name=out_name,
    )

    print(f"dark_threshold={result.dark_threshold}")
    print(f"message={result.message}")
    print(f"regions={len(result.regions)}")
    for i, reg in enumerate(result.regions[:5], start=1):
        print(
            f"  D{i}: ({reg.cx},{reg.cy}) bbox=({reg.x1},{reg.y1})-({reg.x2},{reg.y2}) "
            f"area={reg.area_ratio:.3f} {reg.reason}"
        )
    if result.recommended:
        print(f"recommended_tap=({result.recommended.cx},{result.recommended.cy})")
    if result.annotated_path:
        print(f"annotated={result.annotated_path}")
    return 0 if result.recommended else 2


if __name__ == "__main__":
    raise SystemExit(main())
