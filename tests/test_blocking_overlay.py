"""阻塞弹窗解析器单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.models.server_connectivity_probe import ServerConnectivityProbe
from game_agent.services.blocking_overlay import (
    blank_area_tap_xy,
    detect_blocking_overlay,
    ocr_indicates_blocking_overlay,
    overlay_still_visible,
    probe_indicates_blocking_overlay,
    resolve_dismiss_target,
    verify_overlay_dismissed,
)
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, x1: int, y1: int, x2: int, y2: int) -> OcrBbox:
    return OcrBbox(
        text=text,
        cx=(x1 + x2) // 2,
        cy=(y1 + y2) // 2,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
    )


def test_ocr_indicates_notice_and_daily() -> None:
    assert ocr_indicates_blocking_overlay("Notice 日常通知 Start Game")
    assert not ocr_indicates_blocking_overlay("Start Game only")


def test_probe_blocking_overlay_flag() -> None:
    probe = ServerConnectivityProbe(
        blocking_overlay=True,
        recommendation="dismiss_overlay",
        reason="Notice modal covering screen",
    )
    assert probe_indicates_blocking_overlay(probe)


def test_probe_not_visible_with_modal_reason() -> None:
    probe = ServerConnectivityProbe(
        server_slot_status="not_visible",
        reason="A Notice modal is covering the server slot",
    )
    assert probe_indicates_blocking_overlay(probe)


def test_detect_from_facts() -> None:
    facts = LaunchFacts(announcement_overlay=True)
    result = detect_blocking_overlay(ocr_summary="", facts=facts)
    assert result.suspected
    assert result.source == "facts"


def test_blank_heuristic_avoids_enter_cta() -> None:
    x, y = blank_area_tap_xy(1080, 2400, enter_cta_xy=(538, 1980))
    assert y < 1980 - 150 or abs(x - 538) > 200


def test_blank_heuristic_below_daily_notice_bbox() -> None:
    notice = _bbox("日常通知", 200, 1780, 340, 1820)
    x, y = blank_area_tap_xy(1080, 2400, modal_bbox_hint=notice)
    assert y > notice.y2


def test_verify_overlay_dismissed_on_notice_removed() -> None:
    before = "- (538, 489) 'Notice'\n- (270, 1818) '日常通知'"
    after = "- (539, 1980) 'Start Game'"
    result = verify_overlay_dismissed(before, after)
    assert result.passed


def test_overlay_still_visible() -> None:
    assert overlay_still_visible("Notice 日常通知")
    assert not overlay_still_visible("Start Game only")


def test_resolve_dismiss_blank_heuristic_without_llm() -> None:
    import asyncio

    bboxes = [
        _bbox("Notice", 400, 400, 680, 450),
        _bbox("日常通知", 200, 1780, 340, 1820),
        _bbox("Start Game", 480, 1960, 600, 2000),
    ]
    facts = LaunchFacts(enter_cta_visible=True, enter_cta_xy=(538, 1980))
    plan = asyncio.run(
        resolve_dismiss_target(
            llm_cfg=None,
            screenshot_path=Path("dummy.png"),
            ocr_summary="Notice 日常通知 Start Game",
            bboxes=bboxes,
            screen_w=1080,
            screen_h=2400,
            facts=facts,
        )
    )
    assert plan is not None
    assert plan.method == "blank_heuristic"
    assert plan.y > 0


def test_resolve_dismiss_from_probe_coords() -> None:
    import asyncio

    probe = ServerConnectivityProbe(
        blocking_overlay=True,
        dismiss_tap_x=540,
        dismiss_tap_y=2100,
        recommendation="dismiss_overlay",
    )
    plan = asyncio.run(
        resolve_dismiss_target(
            llm_cfg=None,
            screenshot_path=Path("dummy.png"),
            ocr_summary="",
            bboxes=[],
            screen_w=1080,
            screen_h=2400,
            probe=probe,
        )
    )
    assert plan is not None
    assert plan.method == "probe"
    assert plan.x == 540
    assert plan.y == 2100


_SCREENSHOT_17690 = Path(
    "run_outputs/17690_20260615_151952/attempts/retry_1_20260615_151956/executor/"
    "server_tap2_153624_433044.png"
)


@pytest.mark.skipif(not _SCREENSHOT_17690.is_file(), reason="17690 artifact not present")
def test_17690_screenshot_detects_notice_overlay() -> None:
    from game_agent.utils.ocr_util import extract_text_with_bbox, extract_text_with_bounds

    ocr = extract_text_with_bounds(_SCREENSHOT_17690, device_w=1080, device_h=2400)
    bboxes = extract_text_with_bbox(_SCREENSHOT_17690, device_w=1080, device_h=2400)
    result = detect_blocking_overlay(ocr_summary=ocr, bboxes=bboxes)
    assert result.suspected
    x, y = blank_area_tap_xy(
        1080,
        2400,
        modal_bbox_hint=next(b for b in bboxes if "日常" in b.text),
        enter_cta_xy=(538, 1980),
    )
    assert 1700 < y < 2300
