from __future__ import annotations

from game_agent.services.skill_catalog import read_login_flow_guide

COMPACT_STAGE_HINT = """
=== Login stage cheat sheet (UI varies; classify from OCR) ===
Popups: prefer Agree/Accept/确认/继续/下载 — avoid 拒绝/取消 unless no continue button.
First launch: often privacy/terms before login — tap 同意/接受 (+ checkbox if shown).
Download stage: MB-size confirm dialogs → tap 确认下载/继续, then wait_seconds + re-OCR.
Stages: splash | system_permission | privacy | announcement | login | server_select | download | unknown
Unsure? read_skills_index → read_repo_skill("game_launch_ocr"). Login: fill_credential_field → read VERIFY → fix coords if FAIL → Login tap.
""".strip()

__all__ = ["COMPACT_STAGE_HINT", "read_login_flow_guide"]
