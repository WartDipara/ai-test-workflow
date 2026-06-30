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
        self._cache_dir = Path(cache_dir) if cache_dir else APK_CACHE_DIR
        self._packages_dir = Path(packages_dir) if packages_dir else None
        abis = preserved_abis if preserved_abis else list(DEFAULT_PRESERVED_ABIS)
        self._preserved_abis = frozenset(a.strip() for a in abis if a.strip())
        if not self._preserved_abis:
            self._preserved_abis = DEFAULT_PRESERVED_ABIS

    def run(self, apk_path: Path | None = None) -> PreprocessResult:
        source_apk = apk_path or self._find_source_apk()
        if source_apk is None:
            return PreprocessResult(
                ok=False,
                message=f"No apk_path and no APK in cache: {self._cache_dir}",
            )

        stripped_apk, removed_abis, kept_abis = self._strip_abis(source_apk)
        if stripped_apk is None:
            return PreprocessResult(
                ok=False,
                message="APK ABI strip failed",
                source_apk=source_apk,
            )

        if self._packages_dir is None:
            final_apk = self._finalize_in_cache(stripped_apk, source_apk.name)
        else:
            final_apk = self._move_to_packages(stripped_apk, source_apk.name)
        if final_apk is None:
            dest = "cache" if self._packages_dir is None else "packages/"
            return PreprocessResult(
                ok=False,
                message=f"Failed to move APK to {dest}: {source_apk.name}",
                source_apk=source_apk,
            )

        if stripped_apk != source_apk and source_apk.exists():
            source_apk.unlink()
            logger.info("Removed source APK from apk_cache: %s", source_apk.name)

        kept_sorted = sorted(kept_abis)
        return PreprocessResult(
            ok=True,
            message=(
                f"Preprocess done: {source_apk.name} -> {final_apk.name}, "
                f"kept ABI: {kept_sorted}"
                + (f", removed: {removed_abis}" if removed_abis else "")
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
            logger.warning("apk_cache empty, place source APK in %s", self._cache_dir)
            return None
        if len(candidates) > 1:
            names = ", ".join(p.name for p in candidates)
            logger.warning(
                "Multiple APKs in apk_cache, using first: %s  (all: %s)",
                candidates[0].name,
                names,
            )
        return candidates[0]

    def _strip_abis(self, apk_path: Path) -> tuple[Path | None, list[str], list[str]]:
        logger.info("Checking lib/ ABI dirs in %s...", apk_path.name)
        try:
            with zipfile.ZipFile(apk_path, "r") as zin:
                all_abis = self._collect_all_abis(zin)
                kept = sorted(a for a in all_abis if a in self._preserved_abis)
                removed = sorted(a for a in all_abis if a not in self._preserved_abis)
                if not removed:
                    logger.info(
                        "No ABI stripping needed, lib/ only contains preserved ABIs: %s",
                        kept or sorted(self._preserved_abis),
                    )
                    return apk_path, [], kept or sorted(self._preserved_abis)

                logger.info(
                    "Non-preserved ABIs detected: %s, will only keep %s",
                    removed,
                    sorted(self._preserved_abis),
                )

                output = self._cache_dir / f"._{apk_path.stem}_stripped.apk"
                try:
                    self._write_filtered_zip(zin, output)
                except Exception:
                    output.unlink(missing_ok=True)
                    raise

                logger.info("ABI strip done: %s", output.name)
                return output, removed, kept

        except zipfile.BadZipFile:
            logger.error("%s is not a valid ZIP/APK", apk_path.name)
            return None, [], []
        except OSError as e:
            logger.error("Failed to read APK: %s", e)
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

    def _finalize_in_cache(self, source: Path, target_name: str) -> Path | None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._cache_dir / target_name
        if target.exists() and target.resolve() != source.resolve():
            logger.info("cache already has %s, will overwrite", target_name)
            target.unlink()
        try:
            if source.resolve() != target.resolve():
                shutil.move(str(source), str(target))
            logger.info("APK kept in cache: %s", target)
            return target
        except OSError as e:
            logger.error("Failed to keep APK in cache: %s -> %s: %s", source, target, e)
            return None

    def _move_to_packages(self, source: Path, target_name: str) -> Path | None:
        assert self._packages_dir is not None
        self._packages_dir.mkdir(parents=True, exist_ok=True)
        target = self._packages_dir / target_name

        if target.exists():
            logger.info("packages/ already has %s, will overwrite", target_name)
            target.unlink()

        try:
            shutil.move(str(source), str(target))
            logger.info("APK moved to: %s", target)
            return target
        except OSError as e:
            logger.error("Failed to move APK: %s -> %s: %s", source, target, e)
            return None
