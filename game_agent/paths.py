from __future__ import annotations

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
APK_CACHE_DIR: Path = REPO_ROOT / "apk_cache"


def gameturbo_merged_config_path(gid: str) -> Path:
    """Deprecated shim — use external_services.gameturbo.paths instead."""
    from game_agent.external_services.gameturbo.paths import (
        gameturbo_merged_config_path as _plugin_path,
    )

    return _plugin_path(gid)
