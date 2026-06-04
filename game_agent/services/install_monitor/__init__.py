from __future__ import annotations

import logging

from game_agent.services.adb_service import AdbService
from game_agent.services.install_monitor.base import BaseInstallMonitor, NullInstallMonitor
from game_agent.services.install_monitor.xiaomi import XiaomiInstallMonitor

logger = logging.getLogger(__name__)

_INSTALL_MONITOR_CLASSES: list[type[BaseInstallMonitor]] = [
    XiaomiInstallMonitor,
]


def create_install_monitor(adb: AdbService) -> BaseInstallMonitor:
    """
    根据设备品牌自动选择安装监控适配器。
    遍历所有已知监控类，匹配品牌则返回对应实例；
    无匹配则返回 NullInstallMonitor（空实现）。
    """
    for cls in _INSTALL_MONITOR_CLASSES:
        try:
            monitor = cls()
            if monitor.should_monitor(adb):
                logger.info("安装监控: 匹配 %s", cls.__name__)
                return monitor
        except Exception as e:
            logger.warning("安装监控 %s 初始化失败: %s", cls.__name__, e)
    logger.info("安装监控: 无匹配品牌，使用 NullInstallMonitor")
    return NullInstallMonitor()


__all__ = [
    "BaseInstallMonitor",
    "NullInstallMonitor",
    "XiaomiInstallMonitor",
    "create_install_monitor",
]
