from __future__ import annotations

import logging
from dataclasses import dataclass

from game_agent.services.adb_service import AdbService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DevicePackageCleanupResult:
    package: str
    was_installed: bool
    force_stop: str | None = None
    uninstall: str | None = None
    skipped_reason: str | None = None


def _dedupe_packages(packages: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in packages:
        pkg = (raw or "").strip()
        if not pkg or pkg in seen:
            continue
        seen.add(pkg)
        ordered.append(pkg)
    return ordered


def remove_leftover_game_installations(
    adb: AdbService,
    packages: list[str],
    *,
    require_device: bool = False,
) -> list[DevicePackageCleanupResult]:
    """
    若设备上仍安装指定包，则 force-stop 后 adb uninstall。
    用于新任务开始前与失败收尾，避免上次异常退出遗留的安装干扰 deploy。
    """
    targets = _dedupe_packages(packages)
    if not targets:
        return []

    conn = adb.verify_connection()
    if not conn.startswith("adb connected:"):
        msg = f"adb 不可用，跳过设备卸载: {conn}"
        logger.warning(msg)
        if require_device:
            raise RuntimeError(msg)
        return [
            DevicePackageCleanupResult(
                package=pkg,
                was_installed=False,
                skipped_reason=msg,
            )
            for pkg in targets
        ]

    results: list[DevicePackageCleanupResult] = []
    for pkg in targets:
        if not adb.is_package_installed(pkg):
            logger.info("Device does not have %s, skip uninstall", pkg)
            results.append(
                DevicePackageCleanupResult(
                    package=pkg,
                    was_installed=False,
                    skipped_reason="not_installed",
                ),
            )
            continue

        logger.info("Leftover install %s on device, force-stop then uninstall", pkg)
        fs_out = adb.force_stop_package(pkg)
        un_out = adb.uninstall(pkg)
        still_there = adb.is_package_installed(pkg)
        if still_there:
            logger.warning("After uninstall %s still on device: %s", pkg, un_out)
        else:
            logger.info("Uninstalled leftover %s: %s", pkg, un_out)
        results.append(
            DevicePackageCleanupResult(
                package=pkg,
                was_installed=True,
                force_stop=fs_out,
                uninstall=un_out,
            ),
        )
    return results


def prepare_device_for_new_task(
    adb: AdbService,
    game_package: str,
) -> list[DevicePackageCleanupResult]:
    """新任务开始前：按 TaskRuntime.package_name 清理设备遗留安装。"""
    pkg = (game_package or "").strip()
    if not pkg:
        logger.info("package_name empty, skip device leftover uninstall")
        return []
    return remove_leftover_game_installations(adb, [pkg])
