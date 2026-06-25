"""adb wait_seconds 边界测试。"""

from __future__ import annotations

import time
from unittest.mock import patch

from game_agent.services.adb_service import AdbService


def test_wait_seconds_never_negative_sleep() -> None:
    adb = AdbService.__new__(AdbService)
    with patch("game_agent.services.adb_service.time.sleep") as sleep_mock:
        with patch("game_agent.services.adb_service.time.monotonic") as mono_mock:
            mono_mock.side_effect = [0.0, 0.0, 0.3]
            msg = adb.wait_seconds(0.2)
    assert "Waited" in msg
    for call in sleep_mock.call_args_list:
        assert call.args[0] >= 0
