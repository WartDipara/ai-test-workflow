from __future__ import annotations

import logging
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from game_agent.services.adb_service import AdbService
from game_agent.services.install_monitor.result import InstallMonitorResult
from game_agent.utils.ocr_util import extract_text_with_bounds

logger = logging.getLogger(__name__)

_INSTALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"install", re.IGNORECASE),
    re.compile(r"安装"),
)

_DETAIL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"更多详情"),
    re.compile(r"more\s*details", re.IGNORECASE),
    re.compile(r"detail", re.IGNORECASE),
)

_INSTALL_ANYWAY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"仍要安装"),
    re.compile(r"install\s*anyway", re.IGNORECASE),
    re.compile(r"install\s*still", re.IGNORECASE),
)

_CONTINUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"继续安装"),
    re.compile(r"continue\s*install", re.IGNORECASE),
)


class BaseInstallMonitor(ABC):
    """设备安装安全提示监控基类。"""

    result: InstallMonitorResult

    def __init__(self) -> None:
        self.result = InstallMonitorResult(brand=self.__class__.__name__)

    @abstractmethod
    def brand_pattern(self) -> re.Pattern[str]:
        ...

    def should_monitor(self, adb: AdbService) -> bool:
        try:
            brand = adb.shell("getprop ro.product.brand").strip().lower()
            manufacturer = adb.shell("getprop ro.product.manufacturer").strip().lower()
        except Exception as e:
            logger.warning("无法读取设备品牌: %s", e)
            return False
        self.result.brand = brand or manufacturer
        return bool(
            self.brand_pattern().search(brand)
            or self.brand_pattern().search(manufacturer)
        )

    def monitor_install(
        self,
        adb: AdbService,
        stop_event: threading.Event,
        shot_dir: Path | None = None,
        poll_interval_s: float = 2.0,
    ) -> None:
        """默认空实现——无安装拦截的设备无需处理。"""
        pass

    @staticmethod
    def ocr_screen(adb: AdbService, shot: Path) -> str:
        dw, dh = adb.wm_size()
        return extract_text_with_bounds(shot, device_w=dw, device_h=dh)

    @staticmethod
    def find_coord_by_patterns(
        ocr_text: str,
        patterns: tuple[re.Pattern[str], ...],
    ) -> tuple[int, int] | None:
        for line in ocr_text.splitlines():
            for pat in patterns:
                m = re.search(rf"\((\d+),\s*(\d+)\)\s+.*{pat.pattern}", line, pat.flags)
                if m:
                    return int(m.group(1)), int(m.group(2))
        return None

    def record_click(self) -> None:
        self.result.clicks += 1

    def record_poll(self) -> None:
        self.result.polls += 1

    def record_error(self, msg: str) -> None:
        self.result.errors.append(msg[:500])

    @staticmethod
    def _ocr_and_tap_install(adb: AdbService, shot: Path) -> bool:
        """截图 → OCR → 如果找到 Install 则点击，返回是否已点击。用完即删临时图。"""
        try:
            adb.screencap_png(shot)
        except Exception as e:
            logger.debug("install monitor 截图失败: %s", e)
            return False

        try:
            ocr_text = BaseInstallMonitor.ocr_screen(adb, shot)
        except Exception as e:
            logger.debug("install monitor OCR 失败: %s", e)
            return False
        finally:
            if shot.is_file():
                shot.unlink(missing_ok=True)

        coord = BaseInstallMonitor.find_coord_by_patterns(ocr_text, _INSTALL_PATTERNS)
        if coord is None:
            return False
        x, y = coord
        width, height = adb.wm_size()
        result = adb.tap(x, y, width=width, height=height)
        logger.info("install monitor 已点击 Install 按钮 (%d, %d): %s", x, y, result)
        return True


class NullInstallMonitor(BaseInstallMonitor):
    """默认空实现——不匹配任何品牌，不做任何操作。"""

    _NULL_PATTERN = re.compile(r"(?!x)x")  # never matches

    def brand_pattern(self) -> re.Pattern[str]:
        return self._NULL_PATTERN
