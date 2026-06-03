from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 固定使用仓库根目录下的 config/settings.yaml（与 game_agent 包并列）
_DEFAULT_SETTINGS = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Game test agent (OCR + AI + ADB)")
    parser.parse_args(argv)

    cfg_path = _DEFAULT_SETTINGS.resolve()
    if not cfg_path.is_file():
        print(
            f"错误: 找不到配置文件 {cfg_path}，可复制 config/settings.example.yaml 为 config/settings.yaml",
            file=sys.stderr,
        )
        return 2

    from game_agent.config.loader import load_app_config

    cfg = load_app_config(cfg_path)
    logging.basicConfig(
        level=getattr(logging, cfg.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    from game_agent.controllers.orchestrator import run_orchestrator

    return run_orchestrator(cfg_path)


if __name__ == "__main__":
    raise SystemExit(main())
