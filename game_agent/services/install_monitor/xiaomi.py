from __future__ import annotations

import logging
import re
import tempfile
import threading
from pathlib import Path

import uiautomator2 as u2

from game_agent.services.adb_service import AdbService
from game_agent.services.install_monitor.base import BaseInstallMonitor

logger = logging.getLogger(__name__)


class XiaomiInstallMonitor(BaseInstallMonitor):
    """
    小米/Redmi/POCO 设备安装监控。
    小米在 adb install 时会从底部弹出一个限时 10 秒的安全提示，
    需要点击 Install 按钮才能继续安装。
    优先使用 uiautomator2（无障碍）点击按钮，更可靠。
    """

    _BRAND_PATTERN = re.compile(r"xiaomi|redmi|poco")

    def brand_pattern(self) -> re.Pattern[str]:
        return self._BRAND_PATTERN

    def monitor_install(
        self,
        adb: AdbService,
        stop_event: threading.Event,
        shot_dir: Path | None = None,
        poll_interval_s: float = 1.0,
    ) -> None:
        serial = adb.device_serial
        poll_interval_s = min(poll_interval_s, 1.0)
        logger.info(
            "Xiaomi 安装监控已启动，每 %.1fs 查找 Install 按钮 (serial=%s)",
            poll_interval_s,
            serial,
        )
        if shot_dir is None:
            shot_dir = Path(tempfile.mkdtemp(prefix="install_monitor_"))
        shot_dir.mkdir(parents=True, exist_ok=True)

        try:
            d = u2.connect(serial) if serial else u2.connect()
            d.implicitly_wait(2.0)
        except Exception as e:
            logger.warning("uiautomator2 连接失败，回退 OCR+adb tap: %s", e)
            self.record_error(f"u2_connect_failed: {e}")
            self._monitor_fallback(adb, stop_event, shot_dir, poll_interval_s)
            return

        while not stop_event.is_set():
            self.record_poll()
            clicked = self._u2_click_install(d)
            if clicked:
                self.record_click()
            else:
                logger.debug("install monitor 第 %d 轮: 未检测到 Install 按钮", self.result.polls)
            stop_event.wait(poll_interval_s)

        logger.info(
            "Xiaomi 安装监控已停止（共检查 %d 轮，点击 %d 次）",
            self.result.polls,
            self.result.clicks,
        )

    def _u2_click_install(self, d: u2.Device) -> bool:
        """通过 uiautomator2 查找 Install/安装 按钮并点击。"""
        for text in ("Install", "install", "INSTALL", "安装"):
            try:
                btn = d(text=text)
                if btn.exists(timeout=0.5):
                    btn.click()
                    logger.info("install monitor: u2 已点击 %r", text)
                    return True
            except Exception as e:
                logger.debug("install monitor u2 查找 %r 异常: %s", text, e)
                self.record_error(f"u2_click_{text}: {e}")
        return False

    def _monitor_fallback(
        self,
        adb: AdbService,
        stop_event: threading.Event,
        shot_dir: Path,
        poll_interval_s: float = 1.0,
    ) -> None:
        """uiautomator2 不可用时的回退方案：OCR + adb input tap。"""
        logger.info("install monitor 使用回退方案: OCR+adb tap")
        while not stop_event.is_set():
            self.record_poll()
            shot = shot_dir / f"install_{self.result.polls}.png"
            clicked = self._ocr_and_tap_install(adb, shot)
            if clicked:
                self.record_click()
            else:
                logger.debug(
                    "install monitor(回退) 第 %d 轮: 未检测到 Install 按钮",
                    self.result.polls,
                )
            stop_event.wait(poll_interval_s)
