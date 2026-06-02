"""
预处理阶段：APK ABI 剥离 + 按键精灵脚本推送。

本模块在 retry 循环之前执行一次，不参与重试。

流程：
1. 从 apk_cache/ 找到原始 APK
2. 检查 lib/ 目录，直接移除非 arm64-v8a / armeabi-v7a 的 ABI 目录条目
3. 将处理后的 APK 移动到 packages/ 目录
4. 推送按键精灵脚本到设备
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from game_agent.paths import APK_CACHE_DIR

logger = logging.getLogger(__name__)

# GameTurbo 仅支持这两个 ARM 框架
ALLOWED_ABIS = frozenset({"arm64-v8a", "armeabi-v7a"})

# 按键精灵推送目标路径
_MOBILEANJIAN_COMMANDLIB = "/sdcard/MobileAnJian/commandLib/"
_MOBILEANJIAN_PLUGIN = "/sdcard/MobileAnJian/Plugin/"
_MOBILEANJIAN_SCRIPT = "/sdcard/MobileAnJian/Script/"


@dataclass(slots=True)
class PreprocessResult:
    """预处理阶段的结果。"""

    ok: bool
    message: str
    source_apk: Path | None = None
    processed_apk: Path | None = None
    abis_removed: list[str] = field(default_factory=list)
    abis_kept: list[str] = field(default_factory=list)
    scripts_pushed: int = 0


class Preprocessor:
    """
    APK 预处理 + 脚本推送。

    设计目标：整个任务生命周期仅执行一次，运行于 retry 循环之前。
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        packages_dir: Path | None = None,
        adb_serial: str | None = None,
    ) -> None:
        from game_agent.utils.gameturbo_bootstrap import PACKAGES_DIR

        self._cache_dir = Path(cache_dir) if cache_dir else APK_CACHE_DIR
        self._packages_dir = Path(packages_dir) if packages_dir else PACKAGES_DIR
        self._adb_serial = adb_serial

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    def run(
        self,
        apk_path: Path | None = None,
        script_dir: Path | None = None,
    ) -> PreprocessResult:
        """
        执行 ABI 剥离 + 移动至 packages + 推送脚本。

        Parameters
        ----------
        apk_path : Path | None
            待处理的 APK 路径。为 None 时自动从 cache_dir 中查找。
        script_dir : Path | None
            按键精灵脚本目录。为 None 时不推送脚本。

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

        # 4. 推送脚本
        scripts_pushed = 0
        if script_dir and script_dir.is_dir():
            scripts_pushed = self._push_scripts(script_dir)

        return PreprocessResult(
            ok=True,
            message=(
                f"预处理完成: {source_apk.name} → {final_apk.name}, "
                f"保留 ABI: {sorted(ALLOWED_ABIS)}, "
                f"推送脚本: {scripts_pushed} 个"
            ),
            source_apk=source_apk,
            processed_apk=final_apk,
            abis_kept=sorted(ALLOWED_ABIS),
            scripts_pushed=scripts_pushed,
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

    # ------------------------------------------------------------------
    # Step 4: 推送按键精灵脚本
    # ------------------------------------------------------------------

    def _push_scripts(self, script_dir: Path) -> int:
        """
        将脚本目录下的文件推送到设备上的按键精灵对应路径。

        .mql  → commandLib
        .lua / .luae / .info / .html → Plugin
        .mqb  → 解压后扁平推送到 Script
        """
        if not script_dir.is_dir():
            logger.warning("脚本目录不存在: %s", script_dir)
            return 0

        pushed = 0
        for f in sorted(script_dir.iterdir()):
            if not f.is_file():
                continue
            name = f.name
            if name.endswith(".mql"):
                pushed += self._adb_push_one(f, _MOBILEANJIAN_COMMANDLIB)
            elif name.endswith((".lua", ".luae", ".info", ".html")):
                pushed += self._adb_push_one(f, _MOBILEANJIAN_PLUGIN)
            elif name.endswith(".mqb"):
                pushed += self._push_mqb(f)
            else:
                logger.debug("跳过不支持的文件类型: %s", name)

        logger.info("按键精灵脚本推送完成: %d 个文件", pushed)
        return pushed

    def _adb_push_one(self, local: Path, remote_dir: str) -> int:
        """推送单个文件到设备指定目录。"""
        remote = f"{remote_dir.rstrip('/')}/{local.name}"
        if self._run_adb_push(local, remote):
            logger.info("已推送: %s → %s", local.name, remote)
            return 1
        logger.warning("推送失败: %s → %s", local.name, remote)
        return 0

    def _push_mqb(self, mqb_path: Path) -> int:
        """解压 .mqb 并扁平推送到 Script 目录。"""
        temp_dir = mqb_path.parent / f"._{mqb_path.stem}_extracted"
        try:
            if not self._extract_mqb(mqb_path, temp_dir):
                return 0

            pushed = 0
            base = _MOBILEANJIAN_SCRIPT.rstrip("/")
            for f in sorted(temp_dir.rglob("*")):
                if not f.is_file():
                    continue
                remote = f"{base}/{f.name}"
                if self._run_adb_push(f, remote):
                    pushed += 1
            return pushed
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _extract_mqb(mqb_path: Path, out_dir: Path) -> bool:
        """解压 .mqb（本质是 ZIP 文件）。"""
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(mqb_path, "r") as zf:
                zf.extractall(out_dir)
            return any(out_dir.iterdir())
        except Exception as e:
            logger.warning("解压 .mqb 失败: %s: %s", mqb_path.name, e)
            return False

    def _run_adb_push(self, local: Path, remote: str) -> bool:
        """执行 adb push。"""
        cmd = ["adb"]
        if self._adb_serial:
            cmd.extend(["-s", self._adb_serial])
        cmd.extend(["push", str(local.resolve()), remote])
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return result.returncode == 0
        except FileNotFoundError:
            logger.error("adb 命令未找到，请确认已加入 PATH")
            return False
