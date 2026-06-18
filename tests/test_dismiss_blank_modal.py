"""Blank-dismiss modal tool (OCR + geometry)."""

from __future__ import annotations

from game_agent.services.dismiss_blank_modal import (
    find_blank_dismiss_hint_bbox,
    ocr_indicates_blank_dismiss,
    plan_blank_area_dismiss,
)
from game_agent.utils.ocr_util import OcrBbox


def test_ocr_indicates_blank_dismiss_chinese() -> None:
    assert ocr_indicates_blank_dismiss("获得新技能 点击空白处关闭")


def test_plan_below_modal_hint() -> None:
    bboxes = [
        OcrBbox(text="点击空白处关闭", x1=400, y1=1700, x2=680, y2=1760, cx=540, cy=1730),
    ]
    plan = plan_blank_area_dismiss(
        ocr_summary="获得新技能 点击空白处关闭",
        bboxes=bboxes,
        screen_w=1080,
        screen_h=2400,
    )
    assert plan is not None
    assert plan.method == "below_modal"
    assert plan.y > 1730


def test_plan_no_hint_returns_none() -> None:
    assert (
        plan_blank_area_dismiss(
            ocr_summary="Enter World",
            bboxes=[],
            screen_w=1080,
            screen_h=2400,
        )
        is None
    )


def test_find_hint_bbox() -> None:
    bboxes = [OcrBbox(text="Tap blank to close", x1=10, y1=20, x2=100, y2=40, cx=55, cy=30)]
    found = find_blank_dismiss_hint_bbox(bboxes)
    assert found is not None
    assert found.text.startswith("Tap blank")
