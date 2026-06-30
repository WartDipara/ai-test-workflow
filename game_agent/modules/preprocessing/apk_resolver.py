"""Resolve APK source for preprocessing: download from apks.txt or use cache."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from game_agent.modules.preprocessing.assets_preparer import (
    download_apk,
    download_apk_from_file,
)

logger = logging.getLogger(__name__)

_APKS_TXT_FILENAME = "apks.txt"


class ApkSourceKind(str, Enum):
    DOWNLOADED = "downloaded"
    CACHE = "cache"


@dataclass(frozen=True, slots=True)
class ResolvedApk:
    path: Path
    source: ApkSourceKind


def list_cache_apks(cache_dir: Path) -> list[Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return sorted(p for p in cache_dir.glob("*.apk") if p.is_file())


def resolve_apk_url(
    url: str,
    cache_dir: Path,
    *,
    timeout_s: float = 300.0,
) -> ResolvedApk | None:
    """按指定 URL 下载 APK 到独立 cache 目录。"""
    cache_dir = cache_dir.resolve()
    downloaded = download_apk(url, cache_dir, timeout_s=timeout_s)
    if downloaded is None:
        return None
    logger.info("Preprocess APK source: URL download -> %s", downloaded.name)
    return ResolvedApk(downloaded.resolve(), ApkSourceKind.DOWNLOADED)


def resolve_apk_for_preprocess(cache_dir: Path) -> ResolvedApk | None:
    """
  1. 若 apks.txt 存在且含有效 URL → 下载到 cache_dir
  2. 否则若 cache_dir 已有 *.apk → 使用第一个（多文件时告警）
  3. 否则返回 None
    """
    cache_dir = cache_dir.resolve()
    apks_txt = cache_dir / _APKS_TXT_FILENAME

    if apks_txt.is_file():
        downloaded = download_apk_from_file(apks_txt, cache_dir)
        if downloaded is not None:
            logger.info("Preprocess APK source: download -> %s", downloaded.name)
            return ResolvedApk(downloaded.resolve(), ApkSourceKind.DOWNLOADED)
        logger.warning("apks.txt present but download failed, try cached APK")

    candidates = list_cache_apks(cache_dir)
    if not candidates:
        return None

    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        logger.warning(
            "apk_cache 中存在多个 APK，将使用第一个: %s (全部: %s)",
            candidates[0].name,
            names,
        )

    chosen = candidates[0].resolve()
    logger.info("Preprocess APK source: local cache -> %s", chosen.name)
    return ResolvedApk(chosen, ApkSourceKind.CACHE)


def resolve_failure_message(cache_dir: Path) -> str:
    apks_txt = cache_dir / _APKS_TXT_FILENAME
    if apks_txt.is_file():
        return (
            f"apks.txt 下载失败且 apk_cache 中无可用 APK: {cache_dir}。"
            "请检查链接或手动放入 .apk 文件。"
        )
    return (
        f"apks.txt 不存在: {apks_txt}，且 apk_cache 中无 .apk 文件。"
        "请创建 apks.txt 写入下载链接，或将 APK 放入 apk_cache/。"
    )
