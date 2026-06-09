from __future__ import annotations

import logging
import re
import tempfile
import threading
from pathlib import Path

from game_agent.services.adb_service import AdbService
from game_agent.services.install_monitor.base import BaseInstallMonitor, _CONTINUE_PATTERNS

logger = logging.getLogger(__name__)


class VivoInstallMonitor(BaseInstallMonitor):
    _BRAND_PATTERN = re.compile(r"vivo|bbk|iqoo")

    def brand_pattern(self) -> re.Pattern[str]:
        return self._BRAND_PATTERN

    def monitor_install(
        self,
        adb: AdbService,
        stop_event: threading.Event,
        shot_dir: Path | None = None,
        poll_interval_s: float = 2.0,
    ) -> None:
        logger.info(
            "VIVO 安装监控已启动，每 %.1fs 检测安装拦截页 (serial=%s)",
            poll_interval_s,
            adb.device_serial,
        )
        if shot_dir is None:
            shot_dir = Path(tempfile.mkdtemp(prefix="install_monitor_"))
        shot_dir.mkdir(parents=True, exist_ok=True)
        width, height = adb.wm_size()

        while not stop_event.is_set():
            self.record_poll()
            shot = shot_dir / f"vivo_install_{self.result.polls}.png"
            try:
                adb.screencap_png(shot)
            except Exception as e:
                logger.debug("VIVO 安装监控截图失败: %s", e)
                self.record_error(f"screencap: {e}")
                stop_event.wait(poll_interval_s)
                continue

            try:
                ocr_text = self.ocr_screen(adb, shot)
            except Exception as e:
                logger.debug("VIVO 安装监控 OCR 失败: %s", e)
                self.record_error(f"ocr: {e}")
                self._cleanup(shot)
                stop_event.wait(poll_interval_s)
                continue
            self._cleanup(shot)

            coord = self.find_coord_by_patterns(ocr_text, _CONTINUE_PATTERNS)
            if coord:
                x, y = coord
                adb.tap(x, y, width=width, height=height)
                self.record_click()
                logger.info(
                    "VIVO 安装监控第 %d 轮: 已点击继续安装 (%d, %d)",
                    self.result.polls,
                    x,
                    y,
                )
            else:
                logger.debug("VIVO 安装监控第 %d 轮: 未检测到继续安装按钮", self.result.polls)

            stop_event.wait(poll_interval_s)

        logger.info("VIVO 安装监控已停止（共检查 %d 轮）", self.result.polls)

    @staticmethod
    def _cleanup(shot: Path) -> None:
        if shot.is_file():
            shot.unlink(missing_ok=True)
