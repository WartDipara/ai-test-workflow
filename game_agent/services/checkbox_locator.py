"""隐私协议 checkbox：OCR 协议文字行聚合后向左推导点击坐标（无语义方框本身不做 OCR）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from game_agent.utils.ocr_util import OcrBbox

# 弱关键词：不要求整句识别
_TERMS_WEAK = re.compile(
    r"同意|协议|隐私|许可|政策|条款|已阅读|阅读|agree|term|privacy|policy|license",
    re.IGNORECASE,
)
# 可点击跳转的协议链接文案（勿作 checkbox 左锚）
_LINK_TEXT = re.compile(
    r"许可及服务|服务协议|用户协议|隐私政策|隐私权|政策及|协议及|《|》",
    re.IGNORECASE,
)
# 适龄/CADPA 图标区，勿并入协议行
_AGE_HINT = re.compile(r"适龄|通龄|CADPA|16\+", re.IGNORECASE)
# 左侧前缀说明（「已阅读并同意」类，checkbox 在其左）
_PREFIX_TEXT = re.compile(r"已阅读|阅读|同意|我已", re.IGNORECASE)

_ROW_Y_TOLERANCE_RATIO = 0.55
_ROW_MAX_H_GAP_PX = 140


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

    @property
    def offset_px(self) -> int:
        return self.half_char_px * (1 + self.step)

    def format_message(self, *, prefix: str = "[OCR checkbox]") -> str:
        return (
            f"{prefix} step={self.step} target=({self.cx},{self.cy}) "
            f"line_x1={self.line_x1} half_char={self.half_char_px}px "
            f"offset={self.offset_px}px "
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


def _is_link_bbox(bbox: OcrBbox) -> bool:
    text = bbox.text.strip()
    if not text or _is_age_hint_bbox(bbox):
        return True
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


def _half_char_width(bbox: OcrBbox) -> int:
    """最左 anchor 框：(x2-x1)/字数/2。"""
    width = max(1, bbox.x2 - bbox.x1)
    n = max(1, len(bbox.text.strip()))
    return max(1, width // n // 2)


def checkbox_tap_x(line_x1: int, half_char_px: int, step: int) -> int:
    """step=0 左移 1 半字；每 +1 step 再多移 1 半字。"""
    return line_x1 - half_char_px * (1 + max(0, step))


def locate_privacy_checkbox(
    bboxes: list[OcrBbox],
    screen_width: int,
    screen_height: int,
    *,
    step: int = 0,
) -> CheckboxLocateResult | None:
    """
    从 OCR bbox 聚合协议文字行，以整行最左 x1 为锚点向左推导 checkbox。

    step=0：line_x1 左移 1 半字；step=N 共左移 (N+1) 半字（半字宽取自最左 anchor OCR 框）。
    """
    if not bboxes:
        return None

    row = _pick_terms_row(_cluster_rows(bboxes))
    if row is None:
        return None

    anchors = row.anchor_bboxes()
    anchor = min(anchors, key=lambda b: b.x1)
    line_x1 = row.anchor_x1 if row.anchor_x1 is not None else row.x1
    half_char = _half_char_width(anchor)
    cx = checkbox_tap_x(line_x1, half_char, step)
    cy = (anchor.y1 + anchor.y2) // 2

    if not (0 <= cx < screen_width and 0 <= cy < screen_height):
        return None

    return CheckboxLocateResult(
        cx=cx,
        cy=cy,
        line_x1=line_x1,
        line_y1=row.y1,
        line_x2=row.x2,
        line_y2=row.y2,
        half_char_px=half_char,
        step=step,
        matched_line_text=row.merged_text,
        anchor_bbox_text=anchor.text,
    )


def locate_checkbox_via_ocr(
    bboxes: list[OcrBbox],
    screen_width: int,
    screen_height: int,
    step: int = 0,
) -> tuple[int, int] | None:
    """兼容旧调用：返回 (cx, cy)。"""
    result = locate_privacy_checkbox(
        bboxes,
        screen_width,
        screen_height,
        step=step,
    )
    if result is None:
        return None
    return result.cx, result.cy


# 兼容 agent 旧 import
_TERMS_PATTERNS = (_TERMS_WEAK,)
