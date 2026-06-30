"""对话场景启发式：多数游戏无「继续」按钮，点底部对话框推进。"""

from __future__ import annotations

import re

from game_agent.utils.ocr_util import OcrBbox
from game_agent.i18n import Concept, compile_lexicon_pattern

_OVERLAY_NOISE_RE = re.compile(
    r"GT\[|ms\s*S:|B/s|^\d{1,2}:\d{2}$|CADPA|^\d+\+$",
    re.IGNORECASE,
)
_DIALOGUE_BOTTOM_RATIO = 0.50
_MIN_NARRATIVE_CHARS = 3
_BLANK_CONTINUE_RE = re.compile(
    compile_lexicon_pattern(Concept.DISMISS_CLOSE).pattern
    + r"|轻触.*继续|点按空白|tap\s+.*continue|tap\s+to\s+continue",
    re.IGNORECASE,
)


def _zh_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def is_overlay_noise_text(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    if _OVERLAY_NOISE_RE.search(stripped):
        return True
    if len(stripped) <= 2 and stripped.isascii():
        return True
    return False


def dialogue_bottom_bboxes(
    bboxes: list[OcrBbox],
    screen_h: int,
    *,
    ratio: float = _DIALOGUE_BOTTOM_RATIO,
) -> list[OcrBbox]:
    """屏幕下半区、过滤状态栏/性能浮层后的 OCR 框。"""
    if screen_h <= 0:
        return [b for b in bboxes if not is_overlay_noise_text(b.text or "")]
    cutoff = int(screen_h * ratio)
    out: list[OcrBbox] = []
    for bbox in bboxes:
        text = (bbox.text or "").strip()
        if not text or bbox.cy < cutoff:
            continue
        if is_overlay_noise_text(text):
            continue
        out.append(bbox)
    return out


def narrative_bottom_lines(
    bboxes: list[OcrBbox],
    screen_h: int,
) -> list[OcrBbox]:
    """底部台词/角色名等叙事文字行。"""
    return [
        b
        for b in dialogue_bottom_bboxes(bboxes, screen_h)
        if len((b.text or "").strip()) >= _MIN_NARRATIVE_CHARS
    ]


def pick_dialogue_advance_bbox(
    bboxes: list[OcrBbox],
    *,
    screen_h: int,
) -> OcrBbox | None:
    """
    选择应点击的对话框目标：优先最长台词行（即对话框主体），无按钮指引时照样可点。
    """
    lines = narrative_bottom_lines(bboxes, screen_h)
    if not lines:
        return None
    return max(
        lines,
        key=lambda b: (len((b.text or "").strip()), _zh_count(b.text or ""), b.cy),
    )


def dialogue_box_fallback_xy(screen_w: int, screen_h: int) -> tuple[int, int]:
    """无 OCR 台词时的对话框安全点击区（屏宽居中、偏下）。"""
    w = max(screen_w, 720)
    h = max(screen_h, 1280)
    return int(w * 0.5), int(h * 0.82)


def is_blank_continue_cta(text: str) -> bool:
    return bool(_BLANK_CONTINUE_RE.search((text or "").strip()))


def ocr_has_blank_continue_cta(bboxes: list[OcrBbox]) -> bool:
    return any(is_blank_continue_cta(b.text or "") for b in bboxes)


def score_dialogue_from_bboxes(
    bboxes: list[OcrBbox],
    *,
    screen_h: int,
) -> tuple[float, str]:
    """
    基于底部叙事文字打分；不依赖「点击继续」等 CTA。
    单行台词 + 角色名、或仅一行长台词均可达阈值。
    """
    lines = narrative_bottom_lines(bboxes, screen_h)
    if not lines:
        return 0.0, ""

    score = 0.25
    evidence = [f"bottom_narrative={len(lines)}"]

    long_lines = [b for b in lines if len((b.text or "").strip()) >= 4]
    if long_lines:
        score += 0.2
        evidence.append("narrative_line")

    if len(long_lines) >= 2:
        score += 0.12
        evidence.append("dual_bottom_lines")

    for bbox in long_lines:
        if _zh_count(bbox.text or "") >= 3:
            score += 0.25
            evidence.append("bbox_zh_narrative")
            break

    if len(long_lines) == 1 and _zh_count(long_lines[0].text or "") >= 4:
        score += 0.15
        evidence.append("single_bottom_narrative")

    return min(score, 1.0), ",".join(evidence)
