from __future__ import annotations

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
APK_CACHE_DIR: Path = REPO_ROOT / "apk_cache"
