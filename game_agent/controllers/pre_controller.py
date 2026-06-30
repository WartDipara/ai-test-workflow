from __future__ import annotations

import logging
from pathlib import Path

from game_agent.modules.preprocessing.apk_resolver import (
    resolve_apk_for_preprocess,
    resolve_apk_url,
    resolve_failure_message,
)
from game_agent.modules.preprocessing.preprocessor import Preprocessor, PreprocessResult

logger = logging.getLogger(__name__)


class PreprocessingController:
    def __init__(
        self,
        cache_dir: Path,
        packages_dir: Path | None,
        *,
        preserved_abis: list[str] | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._packages_dir = packages_dir
        self._preserved_abis = preserved_abis

        self._preprocessor = Preprocessor(
            cache_dir=cache_dir,
            packages_dir=packages_dir,
            preserved_abis=preserved_abis,
        )

    def run(self, *, apk_url: str | None = None) -> PreprocessResult:
        if apk_url:
            resolved = resolve_apk_url(apk_url, self._cache_dir)
            if resolved is None:
                return PreprocessResult(
                    ok=False,
                    message=f"APK download failed: {apk_url}",
                )
        else:
            # 无 URL：从任务 cache（或全局 apk_cache）读取已有 APK
            resolved = resolve_apk_for_preprocess(self._cache_dir)
            if resolved is None:
                return PreprocessResult(
                    ok=False,
                    message=resolve_failure_message(self._cache_dir),
                )

        return self._preprocessor.run(apk_path=resolved.path)
