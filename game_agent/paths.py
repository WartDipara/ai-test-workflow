from __future__ import annotations

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent

GAMETURBO_NATIVE_DIR: Path = REPO_ROOT / "GameTurbo-Native"
GAMETURBO_MERGED_CONFIG_PATH: Path = GAMETURBO_NATIVE_DIR / ".gameturbo_merged.json"

APK_CACHE_DIR: Path = REPO_ROOT / "apk_cache"
