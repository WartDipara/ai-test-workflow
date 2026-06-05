from __future__ import annotations

import argparse
import logging
import ssl
import sys
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 300.0
_APKS_TXT_FILENAME = "apks.txt"


def read_apk_urls(apks_txt: Path) -> list[str]:
    """
    从文本文件中读取 APK 下载链接。

    每行一个 URL，忽略空行和以 # 开头的注释行。

    Parameters
    ----------
    apks_txt : Path
        apks.txt 文件路径。

    Returns
    -------
    list[str]
        有效的 URL 列表。
    """
    if not apks_txt.is_file():
        logger.warning("apks.txt 不存在: %s", apks_txt)
        return []

    urls: list[str] = []
    for line in apks_txt.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        urls.append(stripped)

    logger.info("从 %s 读取到 %d 个 APK 下载链接", apks_txt.name, len(urls))
    return urls


def download_apk(
    url: str,
    cache_dir: Path,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> Path | None:
    """
    从 URL 下载 APK 到 cache_dir。

    Parameters
    ----------
    url : str
        APK 下载直链。
    cache_dir : Path
        本地缓存目录（自动创建）。
    timeout_s : float
        下载超时秒数，默认 300s。

    Returns
    -------
    Path | None
        下载后的本地路径，失败返回 None。
    """
    cache_dir = cache_dir.resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    raw_name = url.rstrip("/").rsplit("/", 1)[-1] if "/" in url else "downloaded.apk"
    if not raw_name.endswith(".apk"):
        raw_name += ".apk"
    target = cache_dir / raw_name

    logger.info("正在下载 APK: %s", url)
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_s), follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            content = response.content
    except httpx.HTTPError as e:
        logger.error("APK 下载失败 (HTTP): %s: %s", url, e)
        return None
    except (ssl.SSLError, OSError) as e:
        logger.error(
            "HTTPS 初始化失败，可能是 SSL_CERT_FILE 环境变量指向了不存在的文件。"
            "请尝试执行: unset SSL_CERT_FILE\n  %s",
            e,
        )
        return None

    # 写入文件（先写临时文件再重命名，避免下载中断产生残缺文件）
    logger.info("APK 下载完成 (%d bytes)，正在写入缓存...", len(content))
    tmp = cache_dir / f"._{raw_name}.tmp"
    try:
        tmp.write_bytes(content)
        tmp.rename(target)
    except OSError as e:
        logger.error("APK 写入失败: %s: %s", target, e)
        tmp.unlink(missing_ok=True)
        return None

    size_mb = target.stat().st_size / (1024 * 1024)
    logger.info("APK 写入完成: %s (%.1f MB)", target.name, size_mb)
    return target


def download_apk_from_file(
    apks_txt: Path,
    cache_dir: Path,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> Path | None:
    """
    从 apks.txt 读取第一个有效 URL 并下载到 cache_dir。

    Parameters
    ----------
    apks_txt : Path
        apks.txt 文件路径。
    cache_dir : Path
        本地缓存目录。
    timeout_s : float
        超时秒数。

    Returns
    -------
    Path | None
        下载后的本地路径，无有效 URL 或下载失败返回 None。
    """
    urls = read_apk_urls(apks_txt)
    if not urls:
        return None

    return download_apk(urls[0], cache_dir, timeout_s=timeout_s)


def cli() -> int:
    parser = argparse.ArgumentParser(
        description="从 apks.txt 读取 APK 链接并下载到缓存目录",
    )
    parser.add_argument(
        "--apks-txt", type=Path, default=None,
        help="apks.txt 文件路径 (默认 apk_cache/apks.txt)",
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("./apk_cache"),
        help="APK 缓存目录",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    apks_txt = args.apks_txt or (args.cache_dir / _APKS_TXT_FILENAME)
    result = download_apk_from_file(apks_txt, args.cache_dir)
    if result is None:
        logger.error("APK 下载失败或 apks.txt 无有效链接")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli())
