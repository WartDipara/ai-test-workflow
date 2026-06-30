"""Run adb install with parallel OEM install-dialog monitor (OCR / u2 tap)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TypeVar

from game_agent.services.adb_service import AdbService
from game_agent.services.install_monitor.base import BaseInstallMonitor
from game_agent.services.install_monitor.factory import create_install_monitor
from game_agent.services.install_monitor.result import InstallMonitorResult

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def _run_monitor_thread(
    monitor: BaseInstallMonitor,
    adb: AdbService,
    stop_event: threading.Event,
    shot_dir: Path | None,
    result_holder: list[InstallMonitorResult],
) -> None:
    try:
        monitor.monitor_install(adb, stop_event, shot_dir)
    except Exception as e:
        monitor.result.thread_crashed = True
        monitor.record_error(f"thread_crash: {e}")
        logger.exception("Install monitor thread crashed")
    finally:
        result_holder.append(monitor.result)


def summarize_monitor_result(
    monitor: BaseInstallMonitor,
    monitor_results: list[InstallMonitorResult],
) -> str:
    if monitor_results:
        return monitor_results[0].summary()
    if monitor.result.polls or monitor.result.errors:
        return monitor.result.summary()
    return ""


@dataclass
class InstallMonitorSession:
    """并行安装弹窗监控会话（deploy.sh 与 adb install 共用）。"""

    monitor: BaseInstallMonitor
    shot_dir: Path | None
    _stop_event: threading.Event = field(repr=False)
    _thread: threading.Thread = field(repr=False)
    _results: list[InstallMonitorResult] = field(default_factory=list, repr=False)

    @classmethod
    def start(
        cls,
        adb: AdbService,
        *,
        artifact_root: Path | None = None,
        install_monitor: BaseInstallMonitor | None = None,
    ) -> InstallMonitorSession:
        monitor = install_monitor or create_install_monitor(adb)
        shot_dir = (artifact_root / "install_monitor") if artifact_root else None
        if shot_dir is not None:
            shot_dir.mkdir(parents=True, exist_ok=True)
        stop_event = threading.Event()
        results: list[InstallMonitorResult] = []
        thread = threading.Thread(
            target=_run_monitor_thread,
            args=(monitor, adb, stop_event, shot_dir, results),
            daemon=True,
            name="install_monitor",
        )
        thread.start()
        return cls(
            monitor=monitor,
            shot_dir=shot_dir,
            _stop_event=stop_event,
            _thread=thread,
            _results=results,
        )

    def run_while(self, action: Callable[[], _T], *, join_timeout_s: float = 15.0) -> _T:
        try:
            return action()
        finally:
            self.stop(join_timeout_s=join_timeout_s)

    def stop(self, *, join_timeout_s: float = 15.0) -> str:
        self._stop_event.set()
        self._thread.join(timeout=join_timeout_s)
        if self._thread.is_alive():
            logger.warning("Install monitor thread did not finish within %.0fs", join_timeout_s)
        summary = summarize_monitor_result(self.monitor, self._results)
        if summary:
            logger.info("Install monitor summary: %s", summary)
        return summary


def install_apk_with_monitor(
    adb: AdbService,
    apk_path: Path,
    *,
    timeout_s: float = 300.0,
    artifact_root: Path | None = None,
    install_monitor: BaseInstallMonitor | None = None,
) -> tuple[str, str]:
    """
    ``adb install -r`` 与安装安全弹窗监控并行（小米/三星等 OCR 点 Install）。
    返回 (install_message, monitor_summary)。
    """
    session = InstallMonitorSession.start(
        adb,
        artifact_root=artifact_root,
        install_monitor=install_monitor,
    )
    install_msg = session.run_while(
        lambda: adb.install_apk(apk_path, timeout=timeout_s),
    )
    monitor_summary = summarize_monitor_result(session.monitor, session._results)

    if monitor_summary and artifact_root is not None:
        log_path = artifact_root / "install.log"
        log_path.write_text(
            "\n".join(
                [
                    f"$ adb install -r {apk_path.resolve()}",
                    "",
                    "=== adb install ===",
                    install_msg,
                    "",
                    "=== install_monitor ===",
                    monitor_summary,
                ],
            ),
            encoding="utf-8",
        )

    return install_msg, monitor_summary
