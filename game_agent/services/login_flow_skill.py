from __future__ import annotations

from pathlib import Path

from game_agent.paths import REPO_ROOT

LOGIN_FLOW_SKILL_PATH = REPO_ROOT / "skills" / "game-launch-ocr" / "SKILL.md"

# Short stage hint injected each round (does not replace full SKILL)
COMPACT_STAGE_HINT = """
=== Login stage cheat sheet (UI varies; classify from OCR) ===
Popups: prefer Agree/Accept/确认/继续/下载 — avoid 拒绝/取消 unless no continue button.
First launch: often privacy/terms before login — tap 同意/接受 (+ checkbox if shown).
Download stage: MB-size confirm dialogs → tap 确认下载/继续, then wait_seconds + re-OCR.
Stages: splash | system_permission | privacy | announcement | login | server_select | download | unknown
Call read_login_flow_guide for full skill. Account/password: fill_credential_field then Login.
""".strip()


def read_login_flow_guide(*, max_chars: int = 24_000) -> str:
    path = LOGIN_FLOW_SKILL_PATH
    if not path.is_file():
        return f"[missing] Skill not found at {path}; follow OCR and tool docs."
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n…[skill truncated; prefer live OCR]"
    return text
