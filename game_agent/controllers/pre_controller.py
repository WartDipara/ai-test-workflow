"""
pre_controller：预处理阶段总控。

编排流程：
1. 从 apk_cache/apks.txt 读取 APK 下载链接并下载
2. ABI 剥离 → 移动至 packages/
3. （脚本推送暂留空，待后续接入）
"""

from __future__ import annotations

import logging
from pathlib import Path

from game_agent.modules.preprocessing.assets_preparer import download_apk_from_file
from game_agent.modules.preprocessing.preprocessor import Preprocessor, PreprocessResult

logger = logging.getLogger(__name__)

_APKS_TXT_FILENAME = "apks.txt"


class PreprocessingController:
    """
    预处理总控：APK 下载 + ABI 剥离 + 移动。

    每个模块的总控类集中放在 controllers/ 下，命名遵循 xxx_controller 风格。
    """

    def __init__(
        self,
        cache_dir: Path,
        packages_dir: Path,
        adb_serial: str | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._packages_dir = packages_dir
        self._adb_serial = adb_serial

        self._preprocessor = Preprocessor(
            cache_dir=cache_dir,
            packages_dir=packages_dir,
            adb_serial=adb_serial,
        )

    def run(self) -> PreprocessResult:
        """
        执行完整预处理流程。

        Returns
        -------
        PreprocessResult
        """
        # ── 阶段 1：从 apks.txt 读取链接并下载 APK ──
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

        # ── 阶段 2：ABI 剥离 + 移动至 packages ──
        return self._preprocessor.run(
            apk_path=apk_path,
            script_dir=None,
        )
