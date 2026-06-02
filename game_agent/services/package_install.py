from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from game_agent.services.adb_service import AdbService
from game_agent.services.polling import CALLBACK_HINT, poll_until_sync

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PackageWaitResult:
    ok: bool
    package: str
    polls: int
    aborted: bool = False
    already_installed: bool = False

    def to_tool_message(self) -> str:
        if self.aborted:
            return (
                f"[wait_for_package_installed] Aborted while waiting for {self.package} "
                f"after {self.polls} poll(s). Stop — parallel monitor requested exit."
            )
        if self.ok and self.already_installed:
            return (
                f"[wait_for_package_installed] Package {self.package} already on device. "
                f"{CALLBACK_HINT} Proceed with open_game_app and login flow. "
                "Do not call this tool again."
            )
        if self.ok:
            return (
                f"[wait_for_package_installed] Package {self.package} detected after "
                f"{self.polls} poll(s). {CALLBACK_HINT} Proceed with open_game_app and "
                "login flow. Do not call this tool again."
            )
        return (
            f"[wait_for_package_installed] Timeout: {self.package} not found after "
            f"{self.polls} poll(s). Call report_flow_done(success=false) or verify deploy."
        )


def wait_for_package_installed(
    adb: AdbService,
    package: str,
    *,
    timeout_s: float = 120.0,
    poll_interval_s: float = 2.0,
    should_abort: Callable[[], bool] | None = None,
) -> PackageWaitResult:
    """
    Poll until ``pm path <package>`` shows the APK on device, or timeout/abort.

    One tool invocation runs the full loop (callback when it returns).
    """
    pkg = (package or "").strip()
    if not pkg:
        raise ValueError("package name is empty")

    timeout_s = max(5.0, min(float(timeout_s), 600.0))
    poll_interval_s = max(0.5, min(float(poll_interval_s), 30.0))

    if adb.is_package_installed(pkg):
        logger.info("[PackageWait] already installed: %s", pkg)
        return PackageWaitResult(
            ok=True,
            package=pkg,
            polls=0,
            already_installed=True,
        )

    logger.info(
        "[PackageWait] waiting for %s | timeout %.0fs | interval %.1fs",
        pkg,
        timeout_s,
        poll_interval_s,
    )

    outcome = poll_until_sync(
        predicate=lambda: adb.is_package_installed(pkg),
        timeout_s=timeout_s,
        interval_s=poll_interval_s,
        should_abort=should_abort,
        log_prefix="PackageWait",
    )

    if outcome.aborted:
        return PackageWaitResult(
            ok=False,
            package=pkg,
            polls=outcome.polls,
            aborted=True,
        )
    if outcome.ok:
        logger.info("[PackageWait] detected: %s after %d poll(s)", pkg, outcome.polls)
        return PackageWaitResult(ok=True, package=pkg, polls=outcome.polls)

    logger.warning(
        "[PackageWait] timeout: %s (%.0fs, %d polls)",
        pkg,
        timeout_s,
        outcome.polls,
    )
    return PackageWaitResult(ok=False, package=pkg, polls=outcome.polls)
