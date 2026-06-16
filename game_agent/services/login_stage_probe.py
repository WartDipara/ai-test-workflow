"""OCR-based login / sub-account overlay stage probe."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from game_agent.utils.ocr_util import OcrBbox

LoginProbeStage = Literal["login_form", "sub_account_select", "clear"]

_SUB_ACCOUNT_PANEL_RE = re.compile(
    r"sub-?account|last\s*login|create\s*sub-?account|purchase\s*sub-?account|"
    r"sub-?account\s*description|小号|子账号|选择账号|选择小号|选择角色",
    re.IGNORECASE,
)

_SUB_ACCOUNT_ENTRY_RE = re.compile(
    r"sub-?account\s*\d+|last\s*login|默认|default",
    re.IGNORECASE,
)

_SUB_ACCOUNT_CREATE_PURCHASE_RE = re.compile(
    r"create\s*sub-?account|purchase\s*sub-?account|创建小号|购买小号",
    re.IGNORECASE,
)

_LOGIN_FORM_RE = re.compile(
    r"^(log\s*in|login|登录|立即登录)$|"
    r"account|phone\s*number|email|login\s*password|"
    r"账号|用户名|密码|forgot\s*password|忘记密码",
    re.IGNORECASE,
)

_RIGHT_PANEL_X_RATIO = 0.50
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
                "Action: LangGraph select_sub_account node taps existing entry; "
                "if no OCR coords, ScreenInterpreter supplies tap_target."
            )
        elif self.stage == "login_form":
            lines.append(
                "Action: complete credential login; ignore background enter-game CTA."
            )
        return "\n".join(lines)


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


def _pick_sub_account_entry(bboxes: list[OcrBbox], screen_w: int) -> OcrBbox | None:
    cutoff = int(screen_w * _RIGHT_PANEL_X_RATIO)
    candidates: list[tuple[int, OcrBbox]] = []
    for bbox in bboxes:
        text = bbox.text.strip()
        if not text or bbox.cx < cutoff:
            continue
        if _SUB_ACCOUNT_CREATE_PURCHASE_RE.search(text):
            continue
        if _SUB_ACCOUNT_ENTRY_RE.search(text):
            score = 0
            if re.search(r"last\s*login", text, re.IGNORECASE):
                score += 100
            if re.search(r"sub-?account\s*\d+", text, re.IGNORECASE):
                score += 80
            if re.search(r"default|默认", text, re.IGNORECASE):
                score += 20
            candidates.append((score, bbox))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1].cy))
    return candidates[0][1]


def probe_login_stage(
    bboxes: list[OcrBbox],
    *,
    screen_w: int,
    screen_h: int,
) -> LoginStageProbe:
    """Classify blocking login overlay; right-side panel wins over background enter CTA."""
    _ = screen_h  # reserved for future vertical heuristics

    sub_hits = _right_panel_hits(bboxes, screen_w, _SUB_ACCOUNT_PANEL_RE)
    if sub_hits:
        entry = _pick_sub_account_entry(bboxes, screen_w)
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

    login_hits = _right_panel_hits(bboxes, screen_w, _LOGIN_FORM_RE)
    if len(login_hits) >= _MIN_RIGHT_PANEL_HITS:
        return LoginStageProbe(
            blocking=True,
            stage="login_form",
            reason="right-side login form visible",
        )

    # Fallback: login form hints anywhere (non-overlay games)
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
) -> str | None:
    """If login/sub-account overlay blocks server check, return WRONG_STAGE message."""
    probe = probe_login_stage(bboxes, screen_w=screen_w, screen_h=screen_h)
    if not probe.blocking:
        return None
    detail = "sub-account picker" if probe.stage == "sub_account_select" else "login panel"
    return (
        f"{probe.format_hint()}\n"
        f"[ServerCheck] WRONG_STAGE — {detail} visible; complete login first."
    )
