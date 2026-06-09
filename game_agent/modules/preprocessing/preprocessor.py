from __future__ import annotations

import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from game_agent.paths import APK_CACHE_DIR

logger = logging.getLogger(__name__)

DEFAULT_PRESERVED_ABIS = frozenset({"arm64-v8a", "armeabi-v7a"})


@dataclass(slots=True)
class PreprocessResult:
    ok: bool
    message: str
    source_apk: Path | None = None
    processed_apk: Path | None = None
    abis_removed: list[str] = field(default_factory=list)
    abis_kept: list[str] = field(default_factory=list)


class Preprocessor:
    def __init__(
        self,
        cache_dir: Path | None = None,
        packages_dir: Path | None = None,
        *,
        preserved_abis: list[str] | None = None,
    ) -> None:
        from game_agent.utils.gameturbo_bootstrap import PACKAGES_DIR

        self._cache_dir = Path(cache_dir) if cache_dir else APK_CACHE_DIR
        self._packages_dir = Path(packages_dir) if packages_dir else PACKAGES_DIR
        abis = preserved_abis if preserved_abis else list(DEFAULT_PRESERVED_ABIS)
        self._preserved_abis = frozenset(a.strip() for a in abis if a.strip())
        if not self._preserved_abis:
            self._preserved_abis = DEFAULT_PRESERVED_ABIS

    def run(self, apk_path: Path | None = None) -> PreprocessResult:
        source_apk = apk_path or self._find_source_apk()
        if source_apk is None:
            return PreprocessResult(
                ok=False,
                message=f"未指定 apk_path 且 cache 中未找到 APK 文件: {self._cache_dir}",
            )

        stripped_apk, removed_abis, kept_abis = self._strip_abis(source_apk)
        if stripped_apk is None:
            return PreprocessResult(
                ok=False,
                message="APK ABI 剥离失败",
                source_apk=source_apk,
            )

        final_apk = self._move_to_packages(stripped_apk, source_apk.name)
        if final_apk is None:
            return PreprocessResult(
                ok=False,
                message=f"移动 APK 到 packages/ 失败: {source_apk.name}",
                source_apk=source_apk,
            )

        if stripped_apk != source_apk and source_apk.exists():
            source_apk.unlink()
            logger.info("已清理 apk_cache 中的原始 APK: %s", source_apk.name)

        kept_sorted = sorted(kept_abis)
        return PreprocessResult(
            ok=True,
            message=(
                f"预处理完成: {source_apk.name} → {final_apk.name}, "
                f"保留 ABI: {kept_sorted}"
                + (f", 移除: {removed_abis}" if removed_abis else "")
            ),
            source_apk=source_apk,
            processed_apk=final_apk,
            abis_removed=removed_abis,
            abis_kept=kept_sorted,
        )

    def _find_source_apk(self) -> Path | None:
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

    def _strip_abis(self, apk_path: Path) -> tuple[Path | None, list[str], list[str]]:
        logger.info("正在检查 %s 的 lib/ ABI 目录...", apk_path.name)
        try:
            with zipfile.ZipFile(apk_path, "r") as zin:
                all_abis = self._collect_all_abis(zin)
                kept = sorted(a for a in all_abis if a in self._preserved_abis)
                removed = sorted(a for a in all_abis if a not in self._preserved_abis)
                if not removed:
                    logger.info(
                        "无需 ABI 剥离，lib/ 仅包含保留 ABI: %s",
                        kept or sorted(self._preserved_abis),
                    )
                    return apk_path, [], kept or sorted(self._preserved_abis)

                logger.info(
                    "检测到非保留 ABI: %s，将仅保留 %s",
                    removed,
                    sorted(self._preserved_abis),
                )

                output = self._cache_dir / f"._{apk_path.stem}_stripped.apk"
                try:
                    self._write_filtered_zip(zin, output)
                except Exception:
                    output.unlink(missing_ok=True)
                    raise

                logger.info("ABI 剥离完成: %s", output.name)
                return output, removed, kept

        except zipfile.BadZipFile:
            logger.error("%s 不是有效的 ZIP/APK 文件", apk_path.name)
            return None, [], []
        except OSError as e:
            logger.error("读取 APK 失败: %s", e)
            return None, [], []

    @staticmethod
    def _collect_all_abis(zf: zipfile.ZipFile) -> set[str]:
        abis: set[str] = set()
        for name in zf.namelist():
            if name.startswith("lib/"):
                parts = name.split("/")
                if len(parts) >= 2 and parts[1]:
                    abis.add(parts[1])
        return abis

    def _should_keep_entry(self, filename: str) -> bool:
        if not filename.startswith("lib/"):
            return True
        parts = filename.split("/")
        if len(parts) < 2:
            return True
        abi = parts[1]
        if not abi:
            return True
        return abi in self._preserved_abis

    def _write_filtered_zip(
        self,
        zin: zipfile.ZipFile,
        output_path: Path,
    ) -> None:
        with zipfile.ZipFile(
            output_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zout:
            for item in zin.infolist():
                if not self._should_keep_entry(item.filename):
                    continue
                data = zin.read(item.filename)
                zout.writestr(item, data)

    def _move_to_packages(self, source: Path, target_name: str) -> Path | None:
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
