from __future__ import annotations

import logging

from game_agent.services.adb_service import AdbService
from game_agent.services.install_monitor.base import BaseInstallMonitor, NullInstallMonitor
from game_agent.services.install_monitor.samsung import SamsungInstallMonitor
from game_agent.services.install_monitor.xiaomi import XiaomiInstallMonitor

logger = logging.getLogger(__name__)

_INSTALL_MONITOR_CLASSES: list[type[BaseInstallMonitor]] = [
    XiaomiInstallMonitor,
    SamsungInstallMonitor,
]


def create_install_monitor(adb: AdbService) -> BaseInstallMonitor:
    for cls in _INSTALL_MONITOR_CLASSES:
        try:
            monitor = cls()
            if monitor.should_monitor(adb):
                logger.info("Install monitor: matched %s", cls.__name__)
                return monitor
        except Exception as e:
            logger.warning("Install monitor %s init failed: %s", cls.__name__, e)
    logger.info("Install monitor: no brand match, using NullInstallMonitor")
    return NullInstallMonitor()
