from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from game_agent.external_services.base import PreparedApp
from game_agent.services.adb_service import AdbService
from game_agent.services.install_with_monitor import install_apk_with_monitor
from game_agent.services.package_install import verify_package_on_device

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class InstallResult:
    ok: bool
    message: str
    skipped: bool = False
    install_monitor_summary: str = ""


class CoreAppInstaller:
    """Default core path: adb install -r when package not on device."""

    def __init__(
        self,
        adb: AdbService,
        *,
        install_timeout_s: float = 300.0,
        artifact_root: Path | None = None,
    ) -> None:
        self._adb = adb
        self._install_timeout_s = install_timeout_s
        self._artifact_root = artifact_root

    def install_if_needed(self, prepared: PreparedApp) -> InstallResult:
        pkg = (prepared.package_name or "").strip()
        if prepared.skip_install:
            return InstallResult(ok=True, message="Install skipped by plugin", skipped=True)
        if pkg and self._adb.is_package_installed(pkg):
            logger.info("[CoreInstaller] %s already on device, skip adb install", pkg)
            return InstallResult(ok=True, message=f"Already installed: {pkg}", skipped=True)
        if not prepared.install_apk.is_file():
            return InstallResult(
                ok=False,
                message=f"Install APK missing: {prepared.install_apk}",
            )
        msg, monitor_summary = install_apk_with_monitor(
            self._adb,
            prepared.install_apk,
            timeout_s=self._install_timeout_s,
            artifact_root=self._artifact_root,
        )
        if "Install failed" in msg or "Refused" in msg:
            detail = msg
            if monitor_summary:
                detail = f"{msg} | install_monitor: {monitor_summary}"
            return InstallResult(
                ok=False,
                message=detail,
                install_monitor_summary=monitor_summary,
            )
        if pkg:
            try:
                verify_package_on_device(self._adb, pkg)
            except RuntimeError as e:
                detail = str(e)
                if monitor_summary:
                    detail = f"{detail} | install_monitor: {monitor_summary}"
                return InstallResult(
                    ok=False,
                    message=detail,
                    install_monitor_summary=monitor_summary,
                )
        return InstallResult(
            ok=True,
            message=msg,
            install_monitor_summary=monitor_summary,
        )
