from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from game_agent.services.install_with_monitor import install_apk_with_monitor


def test_install_apk_with_monitor_starts_monitor_thread(tmp_path: Path) -> None:
    adb = MagicMock()
    adb.install_apk.return_value = "Installed: game.apk"
    apk = tmp_path / "game.apk"
    apk.write_bytes(b"x")
    monitor = MagicMock()
    monitor.result.polls = 2
    monitor.result.errors = []
    monitor.result.summary.return_value = "polls=2 clicks=1"

    with patch(
        "game_agent.services.install_with_monitor.create_install_monitor",
        return_value=monitor,
    ):
        msg, summary = install_apk_with_monitor(
            adb,
            apk,
            artifact_root=tmp_path / "art",
        )

    assert msg == "Installed: game.apk"
    assert summary == "polls=2 clicks=1"
    monitor.monitor_install.assert_called_once()
    assert (tmp_path / "art" / "install.log").is_file()
