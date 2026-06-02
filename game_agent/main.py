from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 固定使用仓库根目录下的 config/settings.yaml（与 game_agent 包并列）
_DEFAULT_SETTINGS = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KeyWizard script launcher agent (Pydantic-AI + ADB)")
    parser.parse_args(argv)

    from game_agent.controllers.orchestrator import run_orchestrator

    cfg_path = _DEFAULT_SETTINGS.resolve()
    if not cfg_path.is_file():
        print(
            f"错误: 找不到配置文件 {cfg_path}，可复制 config/settings.example.yaml 为 config/settings.yaml",
            file=sys.stderr,
        )
        return 2

    from game_agent.utils.apk_util import update_settings_yaml_from_apk
    from game_agent.utils.gameturbo_bootstrap import needs_initial_preprocess, output_apk_path

    apk_path = output_apk_path()
    if apk_path.is_file():
        if not update_settings_yaml_from_apk(cfg_path, apk_path):
            print(
                f"警告: 未能从 APK 更新 game 段 ({apk_path})，将使用 settings.yaml 现有值",
                file=sys.stderr,
            )
    elif needs_initial_preprocess():
        print(
            "提示: 尚无 game_gameturbo.apk，仅检测到原包；编排器将在 Init 阶段解析 gid、"
            "准备 games 配置并执行 deploy.sh",
            file=sys.stderr,
        )

    import logging

    from game_agent.config.loader import load_app_config

    cfg = load_app_config(cfg_path)
    logging.basicConfig(
        level=getattr(logging, cfg.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    return run_orchestrator(cfg_path)


if __name__ == "__main__":
    raise SystemExit(main())
