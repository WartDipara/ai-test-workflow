"""GameTurbo-Native filesystem paths (plugin-only; core must not import this module)."""

from __future__ import annotations

from pathlib import Path

from game_agent.paths import REPO_ROOT

GAMETURBO_NATIVE_DIR: Path = REPO_ROOT / "GameTurbo-Native"
GAMETURBO_MERGED_CONFIG_PATH: Path = GAMETURBO_NATIVE_DIR / ".gameturbo_merged.json"
PACKAGES_DIR: Path = GAMETURBO_NATIVE_DIR / "client" / "android" / "packages"
GAMES_DIR: Path = GAMETURBO_NATIVE_DIR / "games"
OUTPUT_APK_NAME = "game_gameturbo.apk"


def gameturbo_merged_config_path(gid: str) -> Path:
    safe_gid = (gid or "").strip() or "unknown"
    return GAMETURBO_NATIVE_DIR / f".gameturbo_merged_{safe_gid}.json"
