from __future__ import annotations

from pathlib import Path

# game_agent/paths.py -> 包目录的上一级为仓库根
REPO_ROOT: Path = Path(__file__).resolve().parent.parent

GAMETURBO_NATIVE_DIR: Path = REPO_ROOT / "GameTurbo-Native"
# deploy.sh 每次执行后更新的合并配置；成功产出应返回此固定路径的副本。
GAMETURBO_MERGED_CONFIG_PATH: Path = GAMETURBO_NATIVE_DIR / ".gameturbo_merged.json"

# APK 缓存区：原包下载后在此处进行预处理（ABI 剥离等），再移动到 packages
APK_CACHE_DIR: Path = REPO_ROOT / "apk_cache"
