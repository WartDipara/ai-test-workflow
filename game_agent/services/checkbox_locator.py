import re

from game_agent.utils.ocr_util import OcrBbox

_TERMS_PATTERNS = [
    re.compile(r"协议|条款|已阅读|同意.*隐私|同意.*政策|read.*agree|agree.*term", re.IGNORECASE),
]
_STEP_PX = 30


def locate_checkbox_via_ocr(
    bboxes: list[OcrBbox],
    screen_width: int,
    screen_height: int,
    step: int = 0,
) -> tuple[int, int] | None:
    """从 OCR bbox 中找到协议文本行，估算其左侧 checkbox 的 tap 坐标。

    step=0 时返回初始估计（左边缘向左偏移半个文本框高度）。
    step>0 时在上一步基础上再向左偏移 30px，直到触及左边缘。
    """
    if not bboxes:
        return None

    for bbox in bboxes:
        if not any(p.search(bbox.text) for p in _TERMS_PATTERNS):
            continue
        ch = bbox.y2 - bbox.y1
        if ch <= 0:
            continue
        base = bbox.x1 - ch // 2
        cy = (bbox.y1 + bbox.y2) // 2
        cx = base - step * _STEP_PX
        if 0 <= cx < screen_width and 0 <= cy < screen_height:
            return (cx, cy)

    return None
