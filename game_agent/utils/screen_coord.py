"""截图与 adb touch 坐标空间对齐（横竖屏 + rotation 兜底）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game_agent.services.adb_service import AdbService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScreenCoordSpace:
    """单帧截图对应的 tap/OCR 有效坐标空间。"""

    tap_w: int
    tap_h: int
    src_w: int
    src_h: int
    rotation: int
    is_landscape: bool
    aspect_corrected: bool

    def layout_width(self) -> int:
        return self.tap_w

    def layout_height(self) -> int:
        return self.tap_h


def _read_image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        w, h = im.size
    return int(w), int(h)


def resolve_screen_coord_space(
    adb: AdbService,
    screenshot_path: Path | str,
    *,
    rotation: int | None = None,
) -> ScreenCoordSpace:
    """
    解析当前截图的 tap/OCR 坐标空间。

    优先使用 adb.touch_size()（含 dumpsys rotation 补偿）；
    若截图宽高比与 touch 不一致，则 swap tap 尺寸以匹配截图（竖屏安全：一致时不改）。
    """
    path = Path(screenshot_path)
    tap_w, tap_h = adb.touch_size()
    rot = rotation if rotation is not None else adb.get_screen_rotation()
    src_w, src_h = _read_image_size(path)

    src_landscape = src_w > src_h
    tap_landscape = tap_w > tap_h
    aspect_corrected = False
    if src_landscape != tap_landscape:
        tap_w, tap_h = tap_h, tap_w
        aspect_corrected = True

    space = ScreenCoordSpace(
        tap_w=tap_w,
        tap_h=tap_h,
        src_w=src_w,
        src_h=src_h,
        rotation=rot,
        is_landscape=src_landscape,
        aspect_corrected=aspect_corrected,
    )
    logger.info(
        "[ScreenCoord] src=%dx%d tap=%dx%d rot=%d landscape=%s corrected=%s",
        src_w,
        src_h,
        tap_w,
        tap_h,
        rot,
        src_landscape,
        aspect_corrected,
    )
    return space


def apply_screen_coord_to_state(state: dict, space: ScreenCoordSpace) -> None:
    state["screen_rotation"] = space.rotation
    state["screen_is_landscape"] = space.is_landscape
    state["screen_aspect_corrected"] = space.aspect_corrected


def sync_deps_screen_size(deps, space: ScreenCoordSpace) -> None:
    deps.screen_width = space.tap_w
    deps.screen_height = space.tap_h


def resolve_and_sync(
    adb: AdbService,
    screenshot_path: Path | str,
    *,
    deps=None,
    state: dict | None = None,
) -> ScreenCoordSpace:
    """解析坐标空间并可选写入 deps / graph state。"""
    space = resolve_screen_coord_space(adb, screenshot_path)
    if deps is not None:
        sync_deps_screen_size(deps, space)
    if state is not None:
        apply_screen_coord_to_state(state, space)
    return space
