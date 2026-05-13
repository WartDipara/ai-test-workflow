from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 固定使用仓库根目录下的 config/settings.yaml（与 game_agent 包并列）
_DEFAULT_SETTINGS = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Game login agent (Pydantic-AI + ADB)")
    parser.parse_args(argv)

    from game_agent.controllers.login_controller import run_login_flow_sync

    cfg_path = _DEFAULT_SETTINGS.resolve()
    if not cfg_path.is_file():
        print(
            f"错误: 找不到配置文件 {cfg_path}，可复制 config/settings.example.yaml 为 config/settings.yaml",
            file=sys.stderr,
        )
        return 2

    state = run_login_flow_sync(cfg_path)
    return 0 if state.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
