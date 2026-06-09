from __future__ import annotations

from unittest.mock import patch

from game_agent.services.adb_devices import list_connected_devices


def test_list_connected_devices_parses_device_state() -> None:
    stdout = "List of devices attached\nemulator-5554\tdevice\noffline-1\toffline\n"
    with patch("game_agent.services.adb_devices.subprocess.run") as run_mock:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = stdout
        run_mock.return_value.stderr = ""
        serials = list_connected_devices()
    assert serials == ["emulator-5554"]
