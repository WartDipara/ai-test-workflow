"""配置相关路径解析（相对路径以仓库根目录为基准）。"""

from __future__ import annotations

from pathlib import Path

from game_agent.paths import REPO_ROOT


def resolve_repo_path(path: Path, *, base: Path | None = None) -> Path:
    """将相对路径解析为绝对路径；默认相对于仓库根目录。"""
    if path.is_absolute():
        return path.resolve()
    root = (base or REPO_ROOT).resolve()
    return (root / path).resolve()
