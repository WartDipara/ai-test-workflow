"""隐私协议 checkbox：OCR 协议文字行聚合后向左推导点击坐标（无语义方框本身不做 OCR）。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from game_agent.utils.ocr_util import OcrBbox
from game_agent.i18n import Concept, compile_lexicon_pattern

if TYPE_CHECKING:
    from game_agent.models.settings import MolmopointSection

logger = logging.getLogger(__name__)

LocateMethod = Literal["ocr_offset", "molmopoint"]

# 弱关键词：不要求整句识别
_TERMS_WEAK = compile_lexicon_pattern(Concept.PRIVACY, Concept.PRIVACY_TERMS, Concept.AGREE)
# 可点击跳转的协议链接文案（勿作 checkbox 左锚）
_LINK_TEXT = compile_lexicon_pattern(Concept.PRIVACY_TERMS)
# 适龄/CADPA 图标区，勿并入协议行
_AGE_HINT = compile_lexicon_pattern(Concept.HEALTH_ADVISORY)
# 左侧前缀说明（「已阅读并同意」类，checkbox 在其左）
_PREFIX_TEXT = compile_lexicon_pattern(Concept.AGREE, Concept.PRIVACY_MODAL_CONSENT)

_ROW_Y_TOLERANCE_RATIO = 0.55
_ROW_MAX_H_GAP_PX = 140


@dataclass(frozen=True, slots=True)
class PrivacyTermsAnchor:
    """OCR 聚合后的协议文字行锚点（不含点击坐标）。"""

    line_x1: int
    line_y1: int
    line_x2: int
    line_y2: int
    cy: int
    line_height: int
    half_char_px: int
    char_width_px: int
    base_offset_px: int
    matched_line_text: str
    anchor_bbox_text: str


@dataclass(frozen=True, slots=True)
class CheckboxLocateResult:
    cx: int
    cy: int
    line_x1: int
    line_y1: int
    line_x2: int
    line_y2: int
    half_char_px: int
    step: int
    matched_line_text: str
    anchor_bbox_text: str
    char_width_px: int = 0
    base_offset_px: int = 0
    locate_method: LocateMethod = "ocr_offset"

    @property
    def offset_px(self) -> int:
        return self.base_offset_px + self.char_width_px * max(0, self.step)

    def format_message(self, *, prefix: str = "[OCR checkbox]") -> str:
        method_tag = "molmo" if self.locate_method == "molmopoint" else f"step={self.step}"
        return (
            f"{prefix} {method_tag} target=({self.cx},{self.cy}) "
            f"line_x1={self.line_x1} base_offset={self.base_offset_px}px "
            f"char_w={self.char_width_px}px "
            f"matched={self.matched_line_text[:80]!r}"
        )


def _vertical_overlap_ratio(a: OcrBbox, b: OcrBbox) -> float:
    top = max(a.y1, b.y1)
    bottom = min(a.y2, b.y2)
    if bottom <= top:
        return 0.0
    overlap = bottom - top
    shorter = min(a.y2 - a.y1, b.y2 - b.y1)
    if shorter <= 0:
        return 0.0
    return overlap / shorter


def _is_age_hint_bbox(bbox: OcrBbox) -> bool:
    return bool(_AGE_HINT.search(bbox.text))


def _is_combined_terms_anchor(bbox: OcrBbox) -> bool:
    """整行协议勾选说明（含链接词）可作 checkbox 左锚。"""
    text = bbox.text.strip()
    if not text or _is_age_hint_bbox(bbox):
        return False
    if not _TERMS_WEAK.search(text):
        return False
    return bool(re.search(r"我已|阅读|同意", text, re.IGNORECASE))


def _is_link_bbox(bbox: OcrBbox) -> bool:
    text = bbox.text.strip()
    if not text or _is_age_hint_bbox(bbox):
        return True
    if _is_combined_terms_anchor(bbox):
        return False
    if not _LINK_TEXT.search(text):
        return False
    # 整句前缀（已阅读并同意…）同框带政策字样时仍作左锚，不是纯链接段
    if _PREFIX_TEXT.search(text) and re.search(r"已阅读|阅读", text):
        return False
    return True


def _is_prefix_bbox(bbox: OcrBbox) -> bool:
    if _is_age_hint_bbox(bbox) or _is_link_bbox(bbox):
        return False
    return bool(_PREFIX_TEXT.search(bbox.text))


def _horizontal_gap(a: OcrBbox, b: OcrBbox) -> int:
    left, right = (a, b) if a.x1 <= b.x1 else (b, a)
    return max(0, right.x1 - left.x2)


def _same_row(a: OcrBbox, b: OcrBbox) -> bool:
    if _is_age_hint_bbox(a) or _is_age_hint_bbox(b):
        return False
    if _horizontal_gap(a, b) > _ROW_MAX_H_GAP_PX:
        return False
    if _vertical_overlap_ratio(a, b) >= _ROW_Y_TOLERANCE_RATIO:
        return True
    cy_a = (a.y1 + a.y2) // 2
    cy_b = (b.y1 + b.y2) // 2
    max_h = max(a.y2 - a.y1, b.y2 - b.y1, 1)
    return abs(cy_a - cy_b) <= int(max_h * _ROW_Y_TOLERANCE_RATIO)


@dataclass(slots=True)
class _TermsRow:
    bboxes: list[OcrBbox]

    def anchor_bboxes(self) -> list[OcrBbox]:
        """checkbox 左锚：仅前缀说明框，排除链接/适龄。"""
        prefixes = [b for b in self.bboxes if _is_prefix_bbox(b)]
        if prefixes:
            return prefixes
        combined = [b for b in self.bboxes if _is_combined_terms_anchor(b)]
        if combined:
            return combined
        fallback = [
            b for b in self.bboxes if not _is_link_bbox(b) and not _is_age_hint_bbox(b)
        ]
        return fallback

    @property
    def anchor_x1(self) -> int | None:
        anchors = self.anchor_bboxes()
        if not anchors:
            return None
        return min(b.x1 for b in anchors)

    @property
    def x1(self) -> int:
        ax = self.anchor_x1
        if ax is not None:
            return ax
        return min(b.x1 for b in self.bboxes)

    @property
    def y1(self) -> int:
        anchors = self.anchor_bboxes()
        if anchors:
            return min(b.y1 for b in anchors)
        return min(b.y1 for b in self.bboxes)

    @property
    def x2(self) -> int:
        anchors = self.anchor_bboxes()
        if anchors:
            return max(b.x2 for b in anchors)
        return max(b.x2 for b in self.bboxes)

    @property
    def y2(self) -> int:
        anchors = self.anchor_bboxes()
        if anchors:
            return max(b.y2 for b in anchors)
        return max(b.y2 for b in self.bboxes)

    @property
    def height(self) -> int:
        return max(1, self.y2 - self.y1)

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2

    @property
    def merged_text(self) -> str:
        ordered = sorted(self.bboxes, key=lambda b: b.x1)
        return "".join(b.text for b in ordered)

    def matches_terms(self) -> bool:
        return bool(_TERMS_WEAK.search(self.merged_text))

    def keyword_score(self) -> int:
        text = self.merged_text
        score = 0
        for kw in ("同意", "协议", "隐私", "许可", "政策", "条款", "阅读"):
            if kw in text:
                score += 1
        return score


def _cluster_rows(bboxes: list[OcrBbox]) -> list[_TermsRow]:
    rows: list[_TermsRow] = []
    for bbox in sorted(bboxes, key=lambda b: (b.y1, b.x1)):
        placed = False
        for row in rows:
            if any(_same_row(bbox, other) for other in row.bboxes):
                row.bboxes.append(bbox)
                placed = True
                break
        if not placed:
            rows.append(_TermsRow(bboxes=[bbox]))
    return rows


def _pick_terms_row(rows: list[_TermsRow]) -> _TermsRow | None:
    candidates = [r for r in rows if r.matches_terms() and r.anchor_x1 is not None]
    if not candidates:
        return None

    def _rank(row: _TermsRow) -> tuple[int, int, int, int]:
        has_prefix = 1 if any(_is_prefix_bbox(b) for b in row.bboxes) else 0
        return (has_prefix, -row.anchor_x1, row.keyword_score(), row.cy)

    return max(candidates, key=_rank)


def _char_width(bbox: OcrBbox) -> int:
    width = max(1, bbox.x2 - bbox.x1)
    n = max(1, len(bbox.text.strip()))
    return max(1, width // n)


def _half_char_width(bbox: OcrBbox) -> int:
    """兼容旧字段：约等于半字宽。"""
    return max(1, _char_width(bbox) // 2)


def _checkbox_base_offset_px(anchor: OcrBbox) -> int:
    """
    文字行左缘到 checkbox 中心的水平距离。
    长整行 OCR 框时不能用「半字」左移，需按字宽 + 间距 + checkbox 半宽估算。
    """
    line_h = max(1, anchor.y2 - anchor.y1)
    cw = _char_width(anchor)
    gap = max(4, cw // 3)
    box_half = max(line_h // 2, cw // 2, 8)
    return cw + gap + box_half


def checkbox_tap_x(
    line_x1: int,
    *,
    base_offset_px: int,
    char_width_px: int,
    step: int = 0,
) -> int:
    """step=0 使用 base_offset；每 +1 step 再左移一字宽。"""
    return line_x1 - base_offset_px - char_width_px * max(0, step)


def find_privacy_terms_anchor(bboxes: list[OcrBbox]) -> PrivacyTermsAnchor | None:
    """从 OCR 聚合协议文字行，返回左锚几何信息（不计算点击点）。"""
    if not bboxes:
        return None

    row = _pick_terms_row(_cluster_rows(bboxes))
    if row is None:
        return None

    anchors = row.anchor_bboxes()
    anchor = min(anchors, key=lambda b: b.x1)
    line_x1 = row.anchor_x1 if row.anchor_x1 is not None else row.x1
    char_w = _char_width(anchor)
    half_char = _half_char_width(anchor)
    base_offset = _checkbox_base_offset_px(anchor)
    cy = (anchor.y1 + anchor.y2) // 2
    line_h = max(1, row.y2 - row.y1)

    return PrivacyTermsAnchor(
        line_x1=line_x1,
        line_y1=row.y1,
        line_x2=row.x2,
        line_y2=row.y2,
        cy=cy,
        line_height=line_h,
        half_char_px=half_char,
        char_width_px=char_w,
        base_offset_px=base_offset,
        matched_line_text=row.merged_text,
        anchor_bbox_text=anchor.text,
    )


def checkbox_tap_from_anchor_offset(
    anchor: PrivacyTermsAnchor,
    *,
    step: int = 0,
) -> tuple[int, int]:
    """基于 OCR 锚点向左偏移推导 checkbox 点击坐标。"""
    cx = checkbox_tap_x(
        anchor.line_x1,
        base_offset_px=anchor.base_offset_px,
        char_width_px=anchor.char_width_px,
        step=step,
    )
    return cx, anchor.cy


def _build_locate_result(
    anchor: PrivacyTermsAnchor,
    cx: int,
    cy: int,
    *,
    step: int,
    locate_method: LocateMethod,
    screen_width: int,
    screen_height: int,
) -> CheckboxLocateResult | None:
    if not (0 <= cx < screen_width and 0 <= cy < screen_height):
        return None
    return CheckboxLocateResult(
        cx=cx,
        cy=cy,
        line_x1=anchor.line_x1,
        line_y1=anchor.line_y1,
        line_x2=anchor.line_x2,
        line_y2=anchor.line_y2,
        half_char_px=anchor.half_char_px,
        step=step,
        matched_line_text=anchor.matched_line_text,
        anchor_bbox_text=anchor.anchor_bbox_text,
        char_width_px=anchor.char_width_px,
        base_offset_px=anchor.base_offset_px,
        locate_method=locate_method,
    )


def locate_checkbox_via_ocr_offset(
    anchor: PrivacyTermsAnchor,
    screen_width: int,
    screen_height: int,
    *,
    step: int = 0,
) -> CheckboxLocateResult | None:
    """子方法：OCR 锚点 + 向左偏移（step 每 +1 再多移一字宽）。"""
    cx, cy = checkbox_tap_from_anchor_offset(anchor, step=step)
    return _build_locate_result(
        anchor,
        cx,
        cy,
        step=step,
        locate_method="ocr_offset",
        screen_width=screen_width,
        screen_height=screen_height,
    )


def validate_molmopoint_point(
    x: float,
    y: float,
    anchor: PrivacyTermsAnchor,
    cfg: MolmopointSection,
) -> bool:
    """判断 MolmoPoint 坐标是否在 OCR 协议行左侧且纵向对齐合理。"""
    max_dy = anchor.line_height * cfg.max_vertical_offset_ratio
    left_margin = anchor.line_x1 - x
    if left_margin < cfg.min_left_of_text_px:
        return False
    if left_margin > cfg.max_left_of_text_px:
        return False
    if abs(y - anchor.cy) > max_dy:
        return False
    return True


def pick_best_molmopoint_point(
    points: list[tuple[float, float]],
    anchor: PrivacyTermsAnchor,
    cfg: MolmopointSection,
) -> tuple[int, int] | None:
    """在多个预测点中选纵向最接近 OCR 锚点中心的有效点。"""
    valid: list[tuple[float, float, float]] = []
    for x, y in points:
        if not validate_molmopoint_point(x, y, anchor, cfg):
            continue
        valid.append((x, y, abs(y - anchor.cy)))
    if not valid:
        return None
    best = min(valid, key=lambda item: (item[2], -item[0]))
    return int(round(best[0])), int(round(best[1]))


def locate_checkbox_via_molmopoint(
    anchor: PrivacyTermsAnchor,
    image_path: Path,
    screen_width: int,
    screen_height: int,
    cfg: MolmopointSection,
) -> CheckboxLocateResult | None:
    """子方法：MolmoPoint 预测 + OCR 锚点校验（仅请求一次）。"""
    from game_agent.services.molmopoint_client import predict_points

    if not cfg.is_active():
        return None
    if not image_path.is_file():
        return None

    points = predict_points(image_path, cfg)
    if not points:
        logger.info("MolmoPoint returned no points, fallback to OCR offset")
        return None

    tap = pick_best_molmopoint_point(points, anchor, cfg)
    if tap is None:
        logger.info(
            "MolmoPoint points rejected by OCR anchor validation (n=%d line_x1=%d cy=%d)",
            len(points),
            anchor.line_x1,
            anchor.cy,
        )
        return None

    cx, cy = tap
    return _build_locate_result(
        anchor,
        cx,
        cy,
        step=0,
        locate_method="molmopoint",
        screen_width=screen_width,
        screen_height=screen_height,
    )


def locate_checkbox_tap(
    bboxes: list[OcrBbox],
    screen_width: int,
    screen_height: int,
    *,
    image_path: Path | None = None,
    molmopoint_cfg: MolmopointSection | None = None,
    step: int = 0,
    try_molmopoint: bool = True,
) -> CheckboxLocateResult | None:
    """
    统一入口：OCR 找协议锚点 → 优先 MolmoPoint（一次）→ 失败则 OCR 左推。
  """
    anchor = find_privacy_terms_anchor(bboxes)
    if anchor is None:
        return None

    if try_molmopoint and image_path is not None and molmopoint_cfg is not None:
        dl = locate_checkbox_via_molmopoint(
            anchor,
            image_path,
            screen_width,
            screen_height,
            molmopoint_cfg,
        )
        if dl is not None:
            return dl

    return locate_checkbox_via_ocr_offset(
        anchor,
        screen_width,
        screen_height,
        step=step,
    )


def locate_privacy_checkbox(
    bboxes: list[OcrBbox],
    screen_width: int,
    screen_height: int,
    *,
    step: int = 0,
    image_path: Path | None = None,
    molmopoint_cfg: MolmopointSection | None = None,
    try_molmopoint: bool = True,
) -> CheckboxLocateResult | None:
    """
    从 OCR bbox 聚合协议文字行并解析 checkbox 点击坐标。

    默认先尝试 MolmoPoint（需 image_path + 配置）；失败则 OCR 左推。
    step 仅作用于 OCR 左推回退路径。
    """
    return locate_checkbox_tap(
        bboxes,
        screen_width,
        screen_height,
        image_path=image_path,
        molmopoint_cfg=molmopoint_cfg,
        step=step,
        try_molmopoint=try_molmopoint,
    )


