import re

from game_agent.services.install_monitor.base import BaseInstallMonitor
from game_agent.services.install_monitor.xiaomi import XiaomiInstallMonitor


def test_find_coord_install_english() -> None:
    ocr = "(120, 340) Install\n(50, 50) Cancel"
    coord = BaseInstallMonitor.find_coord_by_patterns(
        ocr,
        (re.compile(r"install", re.IGNORECASE),),
    )
    assert coord == (120, 340)


def test_xiaomi_matches_redmi_brand() -> None:
    monitor = XiaomiInstallMonitor()
    assert monitor.brand_pattern().search("redmi") is not None
    assert monitor.brand_pattern().search("poco") is not None
