"""区服弹窗 OCR + Vision 融合裁决。"""

from __future__ import annotations

from dataclasses import dataclass

from game_agent.models.server_panel_vision import ServerPanelVisionVerdict
from game_agent.services.server_selector_check import PanelOcrVerdict


@dataclass(frozen=True, slots=True)
class PanelFusionResult:
    passed: bool
    source: str
    message: str


def fuse_panel_verdict(
    *,
    ocr: PanelOcrVerdict,
    vision: ServerPanelVisionVerdict | None,
    min_vision_conf: float = 0.75,
    fusion_enabled: bool = True,
) -> PanelFusionResult:
    """融合矩阵：OCR 快判 + Vision 语义确认。"""
    if ocr.page_navigation:
        return PanelFusionResult(
            passed=False,
            source="hard_veto",
            message="page_navigation (not same-screen overlay)",
        )
    if ocr.enter_moved:
        return PanelFusionResult(
            passed=False,
            source="hard_veto",
            message="enter_cta_moved (full page change)",
        )

    vision_active = fusion_enabled and vision is not None and not vision.parse_failed

    if vision_active:
        v_pass = vision.passed and vision.same_screen and vision.confidence >= min_vision_conf
        if ocr.passed and v_pass:
            return PanelFusionResult(
                passed=True,
                source="both",
                message=(
                    f"ocr={ocr.evidence!r} vision conf={vision.confidence:.2f} "
                    f"reason={vision.reason!r}"
                ),
            )
        if ocr.passed and not vision.passed:
            return PanelFusionResult(
                passed=False,
                source="vision_veto",
                message=(
                    f"ocr={ocr.evidence!r} but vision rejected: {vision.reason!r}"
                ),
            )
        if not ocr.passed and v_pass:
            return PanelFusionResult(
                passed=True,
                source="vision",
                message=(
                    f"vision salvage conf={vision.confidence:.2f} "
                    f"reason={vision.reason!r}"
                ),
            )
        return PanelFusionResult(
            passed=False,
            source="fail",
            message=(
                f"ocr={ocr.evidence!r} vision conf={vision.confidence:.2f} "
                f"passed={vision.passed} same_screen={vision.same_screen} "
                f"reason={vision.reason!r}"
            ),
        )

    if ocr.passed:
        return PanelFusionResult(
            passed=True,
            source="ocr_only",
            message=f"ocr={ocr.evidence!r} (no multimodal)",
        )
    return PanelFusionResult(
        passed=False,
        source="fail",
        message=f"ocr={ocr.evidence!r} (no multimodal)",
    )
