"""OCR-based login / sub-account overlay stage probe."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from game_agent.utils.ocr_util import OcrBbox
from game_agent.i18n import Concept, compile_lexicon_pattern
from game_agent.services.credentials import resolve_sub_account_match_phrases
from game_agent.services.sub_account_locator import pick_sub_account_bbox

LoginProbeStage = Literal["login_form", "sub_account_select", "clear"]

_SUB_ACCOUNT_PANEL_RE = compile_lexicon_pattern(
    Concept.SUB_ACCOUNT,
    Concept.SUB_ACCOUNT_CREATE,
)
_SUB_ACCOUNT_ENTRY_RE = re.compile(
    compile_lexicon_pattern(Concept.SUB_ACCOUNT).pattern + r"|sub-?account\s*\d+",
    re.IGNORECASE,
)
_SUB_ACCOUNT_CREATE_PURCHASE_RE = compile_lexicon_pattern(Concept.SUB_ACCOUNT_CREATE)
_EXCLUDE_META_RE = re.compile(
    r"说明|介绍|描述|help|description|about",
    re.IGNORECASE,
)
_LOGIN_FORM_RE = compile_lexicon_pattern(
    Concept.LOGIN_BUTTON,
    Concept.LOGIN,
    Concept.ACCOUNT_LABEL,
    Concept.PASSWORD_LABEL,
    Concept.FORGOT_PASSWORD,
)
_ENTER_CTA_SPLIT_RE = compile_lexicon_pattern(
    Concept.ENTER_GAME,
    Concept.START_GAME,
    Concept.ENTER_WORLD,
)

_RIGHT_PANEL_X_RATIO = 0.50
_LEFT_PANEL_ENTER_RATIO = 0.55
_RIGHT_PANEL_LOGIN_RATIO = 0.45
_MIN_RIGHT_PANEL_HITS = 2


@dataclass(frozen=True, slots=True)
class LoginStageProbe:
    blocking: bool
    stage: LoginProbeStage
    reason: str
    action_xy: tuple[int, int] | None = None
    action_label: str = ""

    def format_hint(self) -> str:
        lines = [
            f"[LoginStageProbe] stage={self.stage} blocking={str(self.blocking).lower()}",
            f"reason={self.reason}",
        ]
        if self.action_xy is not None:
            lines.append(
                f"target=({self.action_xy[0]},{self.action_xy[1]}) "
                f"'{self.action_label[:80]}'",
            )
        if self.stage == "sub_account_select":
            lines.append(
                "Action: LangGraph select_sub_account node taps credential target row; "
                "if no OCR coords, ScreenInterpreter supplies tap_target."
            )
        elif self.stage == "login_form":
            lines.append(
                "Action: complete credential login; ignore background enter-game CTA."
            )
        return "\n".join(lines)


def split_screen_login_active_reason(reason: str) -> bool:
    return "split_screen_login" in (reason or "")


def detect_split_screen_login(
    bboxes: list[OcrBbox],
    *,
    screen_w: int,
    ocr_merged: str = "",
) -> bool:
    """左侧进游戏 CTA + 右侧登录字段同时可见 → 横屏分屏登录。"""
    _ = ocr_merged
    if screen_w <= 0 or not bboxes:
        return False
    left_cutoff = int(screen_w * _LEFT_PANEL_ENTER_RATIO)
    right_cutoff = int(screen_w * _RIGHT_PANEL_LOGIN_RATIO)
    left_enter = False
    right_login = False
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text:
            continue
        if bbox.cx < left_cutoff and _ENTER_CTA_SPLIT_RE.search(text):
            left_enter = True
        if bbox.cx >= right_cutoff and _LOGIN_FORM_RE.search(text):
            right_login = True
        if left_enter and right_login:
            return True
    return False


def _panel_hits(bboxes: list[OcrBbox], pattern: re.Pattern[str]) -> list[OcrBbox]:
    return [b for b in bboxes if b.text.strip() and pattern.search(b.text.strip())]


def _right_panel_hits(bboxes: list[OcrBbox], screen_w: int, pattern: re.Pattern[str]) -> list[OcrBbox]:
    cutoff = int(screen_w * _RIGHT_PANEL_X_RATIO)
    out: list[OcrBbox] = []
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text or bbox.cx < cutoff:
            continue
        if pattern.search(text):
            out.append(bbox)
    return out


def _left_panel_has_enter_cta(bboxes: list[OcrBbox], screen_w: int) -> bool:
    cutoff = int(screen_w * _LEFT_PANEL_ENTER_RATIO)
    for bbox in bboxes:
        text = bbox.text.strip()
        if text and bbox.cx < cutoff and _ENTER_CTA_SPLIT_RE.search(text):
            return True
    return False


def _pick_sub_account_entry_from(
    bboxes: list[OcrBbox],
    *,
    min_cx: int = 0,
) -> OcrBbox | None:
    """启发式兜底：凭据目标未命中时使用。"""
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text or bbox.cx < min_cx:
            continue
        if _SUB_ACCOUNT_CREATE_PURCHASE_RE.search(text):
            continue
        if _EXCLUDE_META_RE.search(text):
            continue
        if _SUB_ACCOUNT_ENTRY_RE.search(text):
            score = 0
            if re.search(r"last\s*login|上次登录|上次登入", text, re.IGNORECASE):
                score += 100
            if re.search(r"sub-?account\s*\d+", text, re.IGNORECASE):
                score += 80
            if re.search(r"小号\s*\d+|小號\s*\d+", text):
                score += 80
            if re.search(r"default|默认|預設", text, re.IGNORECASE):
                score += 20
            candidates.append((score, bbox))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], -item[1].cy))
    return candidates[0][1]


def _pick_sub_account_entry(
    bboxes: list[OcrBbox],
    screen_w: int,
    *,
    sub_account_phrases: Sequence[str] | None = None,
) -> OcrBbox | None:
    phrases = tuple(sub_account_phrases or resolve_sub_account_match_phrases(None))
    picked = pick_sub_account_bbox(bboxes, target_phrases=phrases, screen_w=screen_w)
    if picked is not None:
        return picked
    cutoff = int(screen_w * _RIGHT_PANEL_X_RATIO)
    return _pick_sub_account_entry_from(bboxes, min_cx=cutoff)


def probe_login_stage(
    bboxes: list[OcrBbox],
    *,
    screen_w: int,
    screen_h: int,
    sub_account_phrases: Sequence[str] | None = None,
) -> LoginStageProbe:
    """Classify blocking login overlay; right-side panel wins over background enter CTA."""
    _ = screen_h  # reserved for future vertical heuristics

    sub_hits = _right_panel_hits(bboxes, screen_w, _SUB_ACCOUNT_PANEL_RE)
    if sub_hits:
        entry = _pick_sub_account_entry(
            bboxes,
            screen_w,
            sub_account_phrases=sub_account_phrases,
        )
        if entry is not None:
            return LoginStageProbe(
                blocking=True,
                stage="sub_account_select",
                reason="right-side sub-account picker visible",
                action_xy=(entry.cx, entry.cy),
                action_label=entry.text,
            )
        return LoginStageProbe(
            blocking=True,
            stage="sub_account_select",
            reason="sub-account panel visible but no existing entry to tap",
        )

    fullscreen_hits = _panel_hits(bboxes, _SUB_ACCOUNT_PANEL_RE)
    if len(fullscreen_hits) >= 1:
        phrases = tuple(sub_account_phrases or resolve_sub_account_match_phrases(None))
        entry = pick_sub_account_bbox(bboxes, target_phrases=phrases, screen_w=screen_w, min_cx=0)
        if entry is None:
            entry = _pick_sub_account_entry_from(bboxes, min_cx=0)
        if entry is not None:
            return LoginStageProbe(
                blocking=True,
                stage="sub_account_select",
                reason="fullscreen sub-account picker visible",
                action_xy=(entry.cx, entry.cy),
                action_label=entry.text,
            )
        return LoginStageProbe(
            blocking=True,
            stage="sub_account_select",
            reason="fullscreen sub-account hints without tap coords",
        )

    if detect_split_screen_login(bboxes, screen_w=screen_w):
        return LoginStageProbe(
            blocking=True,
            stage="login_form",
            reason="split_screen_login: enter CTA left + login form right",
        )

    left_has_enter = _left_panel_has_enter_cta(bboxes, screen_w)
    min_right_hits = 1 if left_has_enter else _MIN_RIGHT_PANEL_HITS

    login_hits = _right_panel_hits(bboxes, screen_w, _LOGIN_FORM_RE)
    if len(login_hits) >= min_right_hits:
        reason = "right-side login form visible"
        if left_has_enter:
            reason = f"split_screen_login: {reason}"
        return LoginStageProbe(
            blocking=True,
            stage="login_form",
            reason=reason,
        )

    login_anywhere = [
        b for b in bboxes
        if b.text.strip() and _LOGIN_FORM_RE.search(b.text.strip())
    ]
    if len(login_anywhere) >= 3:
        return LoginStageProbe(
            blocking=True,
            stage="login_form",
            reason="login form fields visible",
        )

    return LoginStageProbe(
        blocking=False,
        stage="clear",
        reason="no blocking login/sub-account overlay",
    )


def login_stage_gate_message(
    bboxes: list[OcrBbox],
    *,
    screen_w: int,
    screen_h: int,
    sub_account_phrases: Sequence[str] | None = None,
) -> str | None:
    """If login/sub-account overlay blocks server check, return WRONG_STAGE message."""
    probe = probe_login_stage(
        bboxes,
        screen_w=screen_w,
        screen_h=screen_h,
        sub_account_phrases=sub_account_phrases,
    )
    if not probe.blocking:
        return None
    detail = "sub-account picker" if probe.stage == "sub_account_select" else "login panel"
    return (
        f"{probe.format_hint()}\n"
        f"[ServerCheck] WRONG_STAGE — {detail} visible; complete login first."
    )
