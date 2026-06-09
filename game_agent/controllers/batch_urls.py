from __future__ import annotations

import logging
from pathlib import Path

from game_agent.modules.preprocessing.apk_resolver import list_cache_apks
from game_agent.modules.preprocessing.assets_preparer import read_apk_urls

logger = logging.getLogger(__name__)

_APKS_TXT = "apks.txt"


def resolve_batch_urls(cache_dir: Path) -> list[str]:
    """
    解析本轮批跑任务 URL 列表。

    - ``apks.txt`` 中每条有效 URL 对应一个任务
    - 无 ``apks.txt`` 但 ``apk_cache/`` 有 APK 时，返回 ``[""]`` 表示单任务走缓存
    """
    cache_dir = cache_dir.resolve()
    apks_txt = cache_dir / _APKS_TXT
    urls = read_apk_urls(apks_txt) if apks_txt.is_file() else []
    if urls:
        logger.info("批跑任务来源 apks.txt: %d 条 URL", len(urls))
        return urls
    if list_cache_apks(cache_dir):
        logger.info("批跑任务来源 apk_cache 缓存（单任务）")
        return [""]
    return []
