from __future__ import annotations

import logging
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from game_agent.services.adb_service import AdbService
from game_agent.utils.ocr_util import extract_text_with_bounds

logger = logging.getLogger(__name__)


class BaseInstallMonitor(ABC):
    """设备安装安全提示监控基类。"""

    @abstractmethod
    def brand_pattern(self) -> re.Pattern[str]:
        ...

    def should_monitor(self, adb: AdbService) -> bool:
        try:
            brand = adb.shell("getprop ro.product.brand").strip().lower()
        except Exception as e:
            logger.warning("无法读取设备品牌: %s", e)
            return False
        return bool(self.brand_pattern().search(brand))

    def monitor_install(
        self,
        adb: AdbService,
        stop_event: threading.Event,
        poll_interval_s: float = 2.0,
    ) -> None:
        """默认空实现——无安装拦截的设备无需处理。"""
        pass

    @staticmethod
    def _ocr_and_tap_install(adb: AdbService, shot: Path) -> bool:
        """截图 → OCR → 如果找到 Install 则点击，返回是否已点击。用完即删临时图。"""
        try:
            adb.screencap_png(shot)
        except Exception as e:
            logger.debug("install monitor 截图失败: %s", e)
            return False

        dw, dh = adb.wm_size()
        try:
            ocr_text = extract_text_with_bounds(shot, device_w=dw, device_h=dh)
        except Exception as e:
            logger.debug("install monitor OCR 失败: %s", e)
            return False
        finally:
            if shot.is_file():
                shot.unlink(missing_ok=True)

        for line in ocr_text.splitlines():
            line = line.strip()
            if not line:
                continue
            match = re.search(r"\((\d+),\s*(\d+)\)\s+.*install", line, re.IGNORECASE)
            if not match:
                match = re.search(r"\((\d+),\s*(\d+)\)\s+.*安装", line)
            if not match:
                continue
            x, y = int(match.group(1)), int(match.group(2))
            width, height = adb.wm_size()
            result = adb.tap(x, y, width=width, height=height)
            logger.info("install monitor 已点击 Install 按钮 (%d, %d): %s", x, y, result)
            return True

        return False


class NullInstallMonitor(BaseInstallMonitor):
    """默认空实现——不匹配任何品牌，不做任何操作。"""

    _NULL_PATTERN = re.compile(r"(?!x)x")  # never matches

    def brand_pattern(self) -> re.Pattern[str]:
        return self._NULL_PATTERN
