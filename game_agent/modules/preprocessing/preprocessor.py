"""
预处理阶段：APK ABI 剥离。

本模块在 retry 循环之前执行一次，不参与重试。

流程：
1. 从 apk_cache/ 找到原始 APK
2. 检查 lib/ 目录，直接移除非 arm64-v8a / armeabi-v7a 的 ABI 目录条目
3. 将处理后的 APK 移动到 packages/ 目录
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from game_agent.paths import APK_CACHE_DIR

logger = logging.getLogger(__name__)

# GameTurbo 仅支持这两个 ARM 框架
ALLOWED_ABIS = frozenset({"arm64-v8a", "armeabi-v7a"})

@dataclass(slots=True)
class PreprocessResult:
    """预处理阶段的结果。"""

    ok: bool
    message: str
    source_apk: Path | None = None
    processed_apk: Path | None = None
    abis_removed: list[str] = field(default_factory=list)
    abis_kept: list[str] = field(default_factory=list)


class Preprocessor:
    """APK 预处理：ABI 剥离并移动至 packages（任务开始前执行一次）。"""

    def __init__(
        self,
        cache_dir: Path | None = None,
        packages_dir: Path | None = None,
    ) -> None:
        from game_agent.utils.gameturbo_bootstrap import PACKAGES_DIR

        self._cache_dir = Path(cache_dir) if cache_dir else APK_CACHE_DIR
        self._packages_dir = Path(packages_dir) if packages_dir else PACKAGES_DIR

    def run(self, apk_path: Path | None = None) -> PreprocessResult:
        """
        执行 ABI 剥离 + 移动至 packages。

        Parameters
        ----------
        apk_path : Path | None
            待处理的 APK 路径。为 None 时自动从 cache_dir 中查找。

        Returns
        -------
        PreprocessResult
        """
        # 1. 定位原始 APK（外部传入或自动查找）
        source_apk = apk_path or self._find_source_apk()
        if source_apk is None:
            return PreprocessResult(
                ok=False,
                message=f"未指定 apk_path 且 cache 中未找到 APK 文件: {self._cache_dir}",
            )

        # 2. ABI 剥离
        stripped_apk = self._strip_abis(source_apk)
        if stripped_apk is None:
            return PreprocessResult(
                ok=False,
                message="APK ABI 剥离失败",
                source_apk=source_apk,
            )

        # 3. 移动到 packages/
        final_apk = self._move_to_packages(stripped_apk, source_apk.name)
        if final_apk is None:
            return PreprocessResult(
                ok=False,
                message=f"移动 APK 到 packages/ 失败: {source_apk.name}",
                source_apk=source_apk,
            )

        # 3.5 清理 apk_cache/ 中残留的原始 APK
        #     当 ABI 剥离产生了新文件时，原始 APK 仍在 apk_cache/ 中，需要删除
        if stripped_apk != source_apk and source_apk.exists():
            source_apk.unlink()
            logger.info("已清理 apk_cache 中的原始 APK: %s", source_apk.name)

        return PreprocessResult(
            ok=True,
            message=(
                f"预处理完成: {source_apk.name} → {final_apk.name}, "
                f"保留 ABI: {sorted(ALLOWED_ABIS)}"
            ),
            source_apk=source_apk,
            processed_apk=final_apk,
            abis_kept=sorted(ALLOWED_ABIS),
        )

    # ------------------------------------------------------------------
    # Step 1: 定位原始 APK
    # ------------------------------------------------------------------

    def _find_source_apk(self) -> Path | None:
        """在缓存目录中定位原始 APK 文件。"""
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        candidates = sorted(
            p for p in self._cache_dir.glob("*.apk")
            if p.is_file()
        )
        if not candidates:
            logger.warning("apk_cache 目录为空，请将原始 APK 放入 %s", self._cache_dir)
            return None
        if len(candidates) > 1:
            names = ", ".join(p.name for p in candidates)
            logger.warning(
                "apk_cache 中存在多个 APK，将使用第一个: %s  (全部: %s)",
                candidates[0].name,
                names,
            )
        return candidates[0]

    # ------------------------------------------------------------------
    # Step 2: ABI 剥离
    # ------------------------------------------------------------------

    def _strip_abis(self, apk_path: Path) -> Path | None:
        """
        读取 APK（ZIP），过滤掉 lib/ 下所有非 arm64-v8a / armeabi-v7a 的条目，
        其余条目原样保留（不解压/不重新压缩），写入同目录下的新文件。
        """
        logger.info("正在检查 %s 的 lib/ ABI 目录...", apk_path.name)
        try:
            with zipfile.ZipFile(apk_path, "r") as zin:
                removed = self._collect_removed_abis(zin)
                if not removed:
                    logger.info(
                        "无需 ABI 剥离，lib/ 仅包含: %s",
                        sorted(ALLOWED_ABIS & self._collect_all_abis(zin)),
                    )
                    return apk_path  # 无需处理，直接返回原文件

                logger.info(
                    "检测到非 ARM ABI: %s，将仅保留 %s",
                    removed,
                    sorted(ALLOWED_ABIS),
                )

                # 在同目录创建临时文件
                output = self._cache_dir / f"._{apk_path.stem}_stripped.apk"
                try:
                    self._write_filtered_zip(zin, output)
                except Exception:
                    output.unlink(missing_ok=True)
                    raise

                logger.info("ABI 剥离完成: %s", output.name)
                return output

        except zipfile.BadZipFile:
            logger.error("%s 不是有效的 ZIP/APK 文件", apk_path.name)
            return None
        except OSError as e:
            logger.error("读取 APK 失败: %s", e)
            return None

    @staticmethod
    def _collect_all_abis(zf: zipfile.ZipFile) -> set[str]:
        """收集 APK 中 lib/ 下所有 ABI 目录名。"""
        abis: set[str] = set()
        for name in zf.namelist():
            if name.startswith("lib/"):
                parts = name.split("/")
                if len(parts) >= 2 and parts[1]:
                    abis.add(parts[1])
        return abis

    def _collect_removed_abis(self, zf: zipfile.ZipFile) -> list[str]:
        """返回需要移除的 ABI 列表（已排序）。"""
        all_abis = self._collect_all_abis(zf)
        return sorted(a for a in all_abis if a not in ALLOWED_ABIS)

    @staticmethod
    def _should_keep_entry(filename: str) -> bool:
        """判断 ZIP 条目是否应保留。"""
        if not filename.startswith("lib/"):
            return True
        parts = filename.split("/")
        if len(parts) < 2:
            return True
        abi = parts[1]
        if not abi:
            return True
        return abi in ALLOWED_ABIS

    @staticmethod
    def _write_filtered_zip(
        zin: zipfile.ZipFile,
        output_path: Path,
    ) -> None:
        """从源 ZIP 中过滤条目，原样写入新 ZIP（保留原始压缩数据，不解压重压）。"""
        with zipfile.ZipFile(
            output_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zout:
            for item in zin.infolist():
                if not Preprocessor._should_keep_entry(item.filename):
                    continue
                data = zin.read(item.filename)
                zout.writestr(item, data)

    # ------------------------------------------------------------------
    # Step 3: 移动到 packages/
    # ------------------------------------------------------------------

    def _move_to_packages(self, source: Path, target_name: str) -> Path | None:
        """
        将处理后的 APK 移动到 packages/ 目录。
        若目标已存在，先删除。
        """
        self._packages_dir.mkdir(parents=True, exist_ok=True)
        target = self._packages_dir / target_name

        if target.exists():
            logger.info("packages/ 中已存在 %s，将被覆盖", target_name)
            target.unlink()

        try:
            shutil.move(str(source), str(target))
            logger.info("APK 已移动到: %s", target)
            return target
        except OSError as e:
            logger.error("移动 APK 失败: %s → %s: %s", source, target, e)
            return None
