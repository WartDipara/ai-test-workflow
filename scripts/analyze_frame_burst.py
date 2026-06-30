"""analyze_frame_burst — 本地连拍图 OpenCV 分析（人工调试）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from game_agent.models.motion_probe import MotionProbeSection
from game_agent.services.motion_probe import run_motion_probe


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze burst screenshots with motion_probe")
    parser.add_argument(
        "--glob",
        default="screenshot_*.png",
        help="frame glob relative to cwd",
    )
    parser.add_argument("--out", default="motion_probe_analysis", help="output directory")
    args = parser.parse_args()

    paths = sorted(Path().glob(args.glob), key=lambda p: p.name)
    if len(paths) < 2:
        raise SystemExit(f"need >=2 frames, got {len(paths)} for {args.glob}")

    out_dir = Path(args.out)
    result = run_motion_probe(
        paths,
        artifact_root=out_dir,
        round_id=1,
        motion_cfg=MotionProbeSection(),
    )
    summary = {
        "frame_count": len(paths),
        "pairwise_mean_absdiff": result.pairwise_mean_diff,
        "regions": [
            {
                "kind": r.kind,
                "cx": r.cx,
                "cy": r.cy,
                "bbox": list(r.bbox),
                "area": r.area,
                "score": r.score,
                "extra": r.extra,
            }
            for r in result.regions
        ],
        "summary_text": result.summary_text,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
