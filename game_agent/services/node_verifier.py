"""节点完成验证：OCR 文本差分 + 阶段信号。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from game_agent.models.screen_interpretation import ScreenInterpretation
from game_agent.services.privacy_gate import ocr_has_privacy_context, privacy_modal_still_open

from game_agent.i18n import Concept, compile_lexicon_pattern

_PRIVACY_TERMS_RE = compile_lexicon_pattern(Concept.PRIVACY, Concept.PRIVACY_TERMS)
_SUB_ACCOUNT_PANEL_RE = compile_lexicon_pattern(Concept.SUB_ACCOUNT)
_SERVER_SELECT_RE = compile_lexicon_pattern(
    Concept.SERVER_SELECT,
    Concept.SERVER_HINT,
    Concept.ENTER_GAME,
)
_LOGIN_FORM_RE = compile_lexicon_pattern(
    Concept.LOGIN,
    Concept.PASSWORD_LABEL,
    Concept.ACCOUNT_LABEL,
)
_ANNOUNCEMENT_RE = compile_lexicon_pattern(Concept.ANNOUNCEMENT, Concept.OVERLAY)
_CHARACTER_CREATION_RE = compile_lexicon_pattern(Concept.CHARACTER_CREATION, Concept.CHAR_SLOT)


@dataclass(frozen=True, slots=True)
class NodeVerifyResult:
    passed: bool
    reason: str
    evidence: str = ""


def ocr_text_delta_summary(ocr_before: str, ocr_after: str, *, max_lines: int = 6) -> str:
    before_lines = {ln.strip() for ln in (ocr_before or "").splitlines() if ln.strip()}
    after_lines = {ln.strip() for ln in (ocr_after or "").splitlines() if ln.strip()}
    removed = sorted(before_lines - after_lines)[:max_lines]
    added = sorted(after_lines - before_lines)[:max_lines]
    parts: list[str] = []
    if removed:
        parts.append("removed:" + "|".join(s[:40] for s in removed))
    if added:
        parts.append("added:" + "|".join(s[:40] for s in added))
    return "; ".join(parts) if parts else "no_text_delta"


def _signal_hits(text: str, signals: list[str]) -> list[str]:
    merged = text or ""
    hits: list[str] = []
    for sig in signals:
        s = (sig or "").strip()
        if s and s.lower() in merged.lower():
            hits.append(s)
    return hits


def verify_stage_exit(
    *,
    ocr_before: str,
    ocr_after: str,
    expected_stage: str,
    completion_signals: list[str] | None = None,
    interpretation_after: ScreenInterpretation | None = None,
) -> NodeVerifyResult:
    """
  通用节点退出验证：优先 completion_signals，再按 expected_stage 规则，最后看 interpretation。
    """
    stage = (expected_stage or "").strip().lower()
    merged_before = ocr_before or ""
    merged_after = ocr_after or ""
    delta = ocr_text_delta_summary(merged_before, merged_after)

    signals = list(completion_signals or [])
    after_hits = _signal_hits(merged_after, signals)
    if after_hits:
        return NodeVerifyResult(
            passed=True,
            reason=f"completion_signals in after OCR: {after_hits[:3]}",
            evidence=delta,
        )

    if interpretation_after is not None:
        if not interpretation_after.blocking and interpretation_after.stage != stage:
            return NodeVerifyResult(
                passed=True,
                reason=f"interpretation stage={interpretation_after.stage} non-blocking",
                evidence=delta,
            )
        if interpretation_after.stage != stage and stage in interpretation_after.stage:
            return NodeVerifyResult(
                passed=True,
                reason=f"interpretation left {stage}",
                evidence=delta,
            )

    if stage == "privacy_modal":
        before_modal = privacy_modal_still_open(merged_before)
        after_modal = privacy_modal_still_open(merged_after)
        if before_modal and not after_modal:
            return NodeVerifyResult(
                passed=True,
                reason="privacy consent buttons disappeared",
                evidence=delta,
            )
        if before_modal and after_modal:
            return NodeVerifyResult(
                passed=False,
                reason="privacy modal consent row still visible",
                evidence=delta,
            )
        if _LOGIN_FORM_RE.search(merged_after) and not _LOGIN_FORM_RE.search(merged_before):
            return NodeVerifyResult(
                passed=True,
                reason="login form appeared after privacy consent",
                evidence=delta,
            )
        if not before_modal:
            return NodeVerifyResult(
                passed=True,
                reason="no privacy modal consent row before action",
                evidence=delta,
            )
        return NodeVerifyResult(
            passed=False,
            reason="privacy modal still visible",
            evidence=delta,
        )

    if stage in ("sub_account_select", "sub_account"):
        before_panel = bool(_SUB_ACCOUNT_PANEL_RE.search(merged_before))
        after_panel = bool(_SUB_ACCOUNT_PANEL_RE.search(merged_after))
        after_server = bool(_SERVER_SELECT_RE.search(merged_after))
        if before_panel and not after_panel:
            return NodeVerifyResult(
                passed=True,
                reason="sub-account panel text disappeared",
                evidence=delta,
            )
        if after_server and not after_panel:
            return NodeVerifyResult(
                passed=True,
                reason="server/enter screen after sub-account",
                evidence=delta,
            )
        return NodeVerifyResult(
            passed=False,
            reason="still on sub-account screen (panel or no server transition)",
            evidence=delta,
        )

    if stage == "login":
        before_login = bool(_LOGIN_FORM_RE.search(merged_before))
        after_login = bool(_LOGIN_FORM_RE.search(merged_after))
        if before_login and not after_login:
            return NodeVerifyResult(
                passed=True,
                reason="login form text disappeared",
                evidence=delta,
            )
        return NodeVerifyResult(
            passed=False,
            reason="login form still visible",
            evidence=delta,
        )

    if stage == "announcement":
        before_ann = bool(_ANNOUNCEMENT_RE.search(merged_before))
        after_ann = bool(_ANNOUNCEMENT_RE.search(merged_after))
        if before_ann and not after_ann:
            return NodeVerifyResult(
                passed=True,
                reason="announcement text disappeared",
                evidence=delta,
            )
        return NodeVerifyResult(
            passed=False,
            reason="announcement still visible",
            evidence=delta,
        )

    if stage == "server_select":
        before_enter = bool(_SERVER_SELECT_RE.search(merged_before))
        after_enter = bool(_SERVER_SELECT_RE.search(merged_after))
        if before_enter and not after_enter:
            return NodeVerifyResult(
                passed=True,
                reason="server/enter screen text changed after tap",
                evidence=delta,
            )
        if merged_before.strip() != merged_after.strip():
            return NodeVerifyResult(
                passed=True,
                reason="OCR changed after enter tap",
                evidence=delta,
            )
        return NodeVerifyResult(
            passed=False,
            reason="no change after enter tap",
            evidence=delta,
        )

    if stage == "character_creation":
        before_cc = bool(_CHARACTER_CREATION_RE.search(merged_before))
        after_cc = bool(_CHARACTER_CREATION_RE.search(merged_after))
        if before_cc and not after_cc:
            return NodeVerifyResult(
                passed=True,
                reason="character creation text disappeared",
                evidence=delta,
            )
        return NodeVerifyResult(
            passed=False,
            reason="character creation still visible",
            evidence=delta,
        )

    if merged_before.strip() != merged_after.strip():
        return NodeVerifyResult(
            passed=True,
            reason=f"OCR text changed for stage={stage or 'unknown'}",
            evidence=delta,
        )

    return NodeVerifyResult(
        passed=False,
        reason=f"no exit signal for stage={stage or 'unknown'}",
        evidence=delta,
    )
