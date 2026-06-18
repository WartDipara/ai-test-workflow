"""Core APK identity helpers (no native plugin directory dependency)."""

from __future__ import annotations

import re
from pathlib import Path

_GID_RE = re.compile(r"^(\d+)")


def parse_gid_from_apk_name(apk_path: Path) -> str:
    match = _GID_RE.match(apk_path.name)
    if not match:
        raise RuntimeError(
            f"无法从 APK 文件名解析 gid: {apk_path.name}。"
            "文件名需以数字 gid 开头（如 12345_game.apk）。",
        )
    return match.group(1)


def peek_gid_from_cache(cache_dir: Path) -> str | None:
    cache_dir = cache_dir.resolve()
    if not cache_dir.is_dir():
        return None
    candidates = sorted(p for p in cache_dir.glob("*.apk") if p.is_file())
    for apk in candidates:
        try:
            return parse_gid_from_apk_name(apk)
        except RuntimeError:
            continue
    return None


def resolve_task_gid(
    gid: str = "",
    *,
    cache_dir: Path | None = None,
) -> str:
    resolved = (gid or "").strip()
    if resolved:
        return resolved
    if cache_dir is not None:
        peeked = peek_gid_from_cache(cache_dir)
        if peeked:
            return peeked
    return "unknown"
