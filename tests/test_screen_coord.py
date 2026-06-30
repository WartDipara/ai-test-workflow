from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from game_agent.utils.screen_coord import resolve_screen_coord_space


def _write_png(path: Path, w: int, h: int) -> None:
    Image.new("RGB", (w, h), color="white").save(path)


@pytest.fixture
def adb() -> MagicMock:
    mock = MagicMock()
    mock.get_screen_rotation.return_value = 0
    return mock


def test_portrait_aligned_no_correction(tmp_path: Path, adb: MagicMock) -> None:
    shot = tmp_path / "portrait.png"
    _write_png(shot, 1260, 2800)
    adb.touch_size.return_value = (1260, 2800)

    space = resolve_screen_coord_space(adb, shot)

    assert space.aspect_corrected is False
    assert space.tap_w == 1260
    assert space.tap_h == 2800
    assert space.is_landscape is False


def test_landscape_mismatch_swaps_touch(tmp_path: Path, adb: MagicMock) -> None:
    shot = tmp_path / "landscape.png"
    _write_png(shot, 2800, 1260)
    adb.touch_size.return_value = (1260, 2800)

    space = resolve_screen_coord_space(adb, shot, rotation=0)

    assert space.aspect_corrected is True
    assert space.tap_w == 2800
    assert space.tap_h == 1260
    assert space.is_landscape is True


def test_landscape_rotation_ok_no_swap(tmp_path: Path, adb: MagicMock) -> None:
    shot = tmp_path / "landscape_rot.png"
    _write_png(shot, 2800, 1260)
    adb.touch_size.return_value = (2800, 1260)
    adb.get_screen_rotation.return_value = 90

    space = resolve_screen_coord_space(adb, shot)

    assert space.aspect_corrected is False
    assert space.tap_w == 2800
    assert space.tap_h == 1260
