from __future__ import annotations

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent

GAMETURBO_NATIVE_DIR: Path = REPO_ROOT / "GameTurbo-Native"
GAMETURBO_MERGED_CONFIG_PATH: Path = GAMETURBO_NATIVE_DIR / ".gameturbo_merged.json"


def gameturbo_merged_config_path(gid: str) -> Path:
    """按 gid 返回 deploy 合并配置路径（批跑隔离）。"""
    safe_gid = (gid or "").strip() or "unknown"
    return GAMETURBO_NATIVE_DIR / f".gameturbo_merged_{safe_gid}.json"

APK_CACHE_DIR: Path = REPO_ROOT / "apk_cache"
