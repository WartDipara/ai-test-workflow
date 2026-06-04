from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_DISMISS_TEXT_PATTERNS = (
    "我已知晓",
    "我知道了",
    "确定",
    "关闭",
    "关 闭",
    "取消",
    "OK",
    "Confirm",
    "Close",
)


def dismiss_overlay(serial: str | None, width: int, height: int) -> str:
    """
    尝试关闭当前界面上的蒙版/公告/弹窗。
    优先级：uiautomator2 查找已知文本按钮 → 点右上角区域 → adb back。
    """
    steps: list[str] = []

    # 1. uiautomator2 查找已知文本按钮
    try:
        import uiautomator2 as u2

        d = u2.connect(serial) if serial else u2.connect()
        d.implicitly_wait(1.0)
        for text in _DISMISS_TEXT_PATTERNS:
            try:
                btn = d(text=text)
                if btn.exists(timeout=0.5):
                    btn.click()
                    steps.append(f"u2 点击 {text!r}")
                    break
            except Exception:
                continue
        else:
            # 2. 没有找到已知文本按钮，点右上角
            corner_x = int(width * 0.92)
            corner_y = int(height * 0.08)
            d.click(corner_x, corner_y)
            steps.append(f"u2 点右上角 ({corner_x},{corner_y})")
    except ImportError:
        steps.append("uiautomator2 未安装")
    except Exception as e:
        steps.append(f"uiautomator2 失败: {e}")

    time.sleep(0.5)

    # 3. 尝试 adb back（不影响已关闭的弹窗）
    try:
        from game_agent.services.adb_service import AdbService

        adb = AdbService(serial)
        adb.press_back()
        steps.append("adb back")
    except Exception as e:
        steps.append(f"adb back 失败: {e}")

    return " | ".join(steps)
