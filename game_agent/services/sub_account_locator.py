"""凭据目标 + OCR 精确匹配小号选择行。"""

from __future__ import annotations

import re
from collections.abc import Sequence

from game_agent.i18n import Concept, compile_lexicon_pattern
from game_agent.services.credentials import GameCredentials, resolve_sub_account_match_phrases
from game_agent.utils.ocr_util import OcrBbox

_RIGHT_PANEL_X_RATIO = 0.50

_SUB_ACCOUNT_CREATE_PURCHASE_RE = compile_lexicon_pattern(Concept.SUB_ACCOUNT_CREATE)
_EXCLUDE_META_RE = re.compile(
    r"说明|介绍|描述|help|description|about",
    re.IGNORECASE,
)
_LAST_LOGIN_RE = re.compile(r"last\s*login|上次登录|最近登录|上次登入", re.IGNORECASE)

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_sub_account_compare(text: str) -> str:
    """casefold + 全角数字归一 + 折叠空白（用于相等比较）。"""
    t = (text or "").strip().casefold()
    t = t.translate(_FULLWIDTH_DIGITS)
    return re.sub(r"\s+", "", t)


def _phrase_variants_for_match(phrase: str) -> tuple[str, ...]:
    """生成 OCR 常见变体（空格、全角数字）。"""
    base = (phrase or "").strip()
    if not base:
        return ()
    seen: set[str] = {base}
    out: list[str] = [base]
    spaced = re.sub(r"(\D)(\d)", r"\1 \2", base)
    if spaced not in seen:
        seen.add(spaced)
        out.append(spaced)
    fw = base.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
    if fw not in seen:
        seen.add(fw)
        out.append(fw)
    return tuple(out)


def _is_excluded_sub_account_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _SUB_ACCOUNT_CREATE_PURCHASE_RE.search(t):
        return True
    return bool(_EXCLUDE_META_RE.search(t))


def _phrase_matches_text(phrase: str, text: str) -> tuple[bool, bool]:
    """返回 (matched, exact)。"""
    raw_text = (text or "").strip()
    if not raw_text or _is_excluded_sub_account_text(raw_text):
        return False, False

    for variant in _phrase_variants_for_match(phrase):
        norm_text = normalize_sub_account_compare(raw_text)
        norm_phrase = normalize_sub_account_compare(variant)
        if norm_text == norm_phrase:
            return True, True
        if variant.isascii():
            if re.search(rf"\b{re.escape(variant.strip())}\b", raw_text, re.IGNORECASE):
                return True, False
        elif norm_phrase and norm_phrase in norm_text:
            return True, False
    return False, False


def _score_bbox(bbox: OcrBbox, *, exact: bool, screen_w: int) -> int:
    score = 200 if exact else 100
    text = bbox.text or ""
    if _LAST_LOGIN_RE.search(text):
        score += 50
    cutoff = int(screen_w * _RIGHT_PANEL_X_RATIO)
    if bbox.cx >= cutoff:
        score += 10
    return score


def pick_sub_account_bbox(
    bboxes: list[OcrBbox],
    *,
    target_phrases: Sequence[str],
    screen_w: int,
    min_cx: int | None = None,
) -> OcrBbox | None:
    """在 bboxes 中按凭据目标短语选小号行（英文大小写不敏感）。"""
    if not bboxes or not target_phrases:
        return None

    cutoff = int(screen_w * _RIGHT_PANEL_X_RATIO) if min_cx is None else min_cx
    candidates: list[tuple[int, int, OcrBbox]] = []

    for bbox in bboxes:
        if bbox.cx < cutoff:
            continue
        text = (bbox.text or "").strip()
        if not text or _is_excluded_sub_account_text(text):
            continue
        best_local = 0
        is_exact = False
        for phrase in target_phrases:
            matched, exact = _phrase_matches_text(phrase, text)
            if not matched:
                continue
            local = _score_bbox(bbox, exact=exact, screen_w=screen_w)
            if local > best_local or (local == best_local and exact):
                best_local = local
                is_exact = exact
        if best_local > 0:
            candidates.append((best_local, 1 if is_exact else 0, bbox))

    if not candidates:
        for bbox in bboxes:
            text = (bbox.text or "").strip()
            if min_cx is not None and bbox.cx < min_cx:
                continue
            if not text or _is_excluded_sub_account_text(text):
                continue
            best_local = 0
            is_exact = False
            for phrase in target_phrases:
                matched, exact = _phrase_matches_text(phrase, text)
                if not matched:
                    continue
                local = _score_bbox(bbox, exact=exact, screen_w=screen_w)
                if local > best_local or (local == best_local and exact):
                    best_local = local
                    is_exact = exact
            if best_local > 0:
                candidates.append((best_local, 1 if is_exact else 0, bbox))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], -item[1], -item[2].cy))
    return candidates[0][2]


def pick_sub_account_for_credentials(
    bboxes: list[OcrBbox],
    *,
    credentials: GameCredentials | None,
    screen_w: int,
) -> OcrBbox | None:
    phrases = resolve_sub_account_match_phrases(credentials)
    return pick_sub_account_bbox(bboxes, target_phrases=phrases, screen_w=screen_w)
