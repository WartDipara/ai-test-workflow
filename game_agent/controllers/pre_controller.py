from __future__ import annotations

import logging
from pathlib import Path

from game_agent.modules.preprocessing.assets_preparer import download_apk_from_file
from game_agent.modules.preprocessing.preprocessor import Preprocessor, PreprocessResult

logger = logging.getLogger(__name__)

_APKS_TXT_FILENAME = "apks.txt"


class PreprocessingController:
    def __init__(
        self,
        cache_dir: Path,
        packages_dir: Path,
    ) -> None:
        self._cache_dir = cache_dir
        self._packages_dir = packages_dir

        self._preprocessor = Preprocessor(
            cache_dir=cache_dir,
            packages_dir=packages_dir,
        )

    def run(self) -> PreprocessResult:
        apks_txt = self._cache_dir / _APKS_TXT_FILENAME
        apk_path = download_apk_from_file(apks_txt, self._cache_dir)
        if apk_path is None:
            return PreprocessResult(
                ok=False,
                message=(
                    f"apks.txt 中无有效链接或下载失败: {apks_txt}"
                    if apks_txt.is_file()
                    else f"apks.txt 不存在: {apks_txt}；请创建并写入 APK 下载链接"
                ),
            )

        return self._preprocessor.run(apk_path=apk_path)
