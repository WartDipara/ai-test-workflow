from __future__ import annotations

from pathlib import Path

from game_agent.paths import REPO_ROOT

SKILLS_DIR = REPO_ROOT / "skills"
SKILLS_INDEX_PATH = SKILLS_DIR / "SKILL.md"

# skill_id → filename under skills/
REPO_SKILL_FILES: dict[str, str] = {
    "game_launch_ocr": "game_launch_ocr_skill.md",
    "gameturbo_log_baseline": "gameturbo_log_baseline_skill.md",
}

_ALIASES: dict[str, str] = {
    "game-launch-ocr": "game_launch_ocr",
    "gameturbo-log-baseline": "gameturbo_log_baseline",
    "login": "game_launch_ocr",
    "login_flow": "game_launch_ocr",
}


def normalize_skill_id(skill_id: str) -> str | None:
    key = (skill_id or "").strip().lower().replace("-", "_")
    if not key:
        return None
    if key in REPO_SKILL_FILES:
        return key
    dashed = key.replace("_", "-")
    if dashed in _ALIASES:
        return _ALIASES[dashed]
    if key in _ALIASES:
        return _ALIASES[key]
    return None


def list_repo_skill_ids() -> list[str]:
    return sorted(REPO_SKILL_FILES.keys())


def read_skills_index(*, max_chars: int = 16_000) -> str:
    if not SKILLS_INDEX_PATH.is_file():
        return f"[missing] Skill index not found at {SKILLS_INDEX_PATH}"
    text = SKILLS_INDEX_PATH.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n…[index truncated]"
    return text


def read_repo_skill(skill_id: str, *, max_chars: int = 48_000) -> str:
    normalized = normalize_skill_id(skill_id)
    if normalized is None:
        ids = ", ".join(list_repo_skill_ids())
        return (
            f"Unknown skill_id `{skill_id}`. "
            f"Call read_skills_index first. Known ids: {ids}"
        )
    filename = REPO_SKILL_FILES[normalized]
    path = SKILLS_DIR / filename
    if not path.is_file():
        return f"[missing] Skill file not found: {path}"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n…[skill truncated; prefer live OCR/tools]"
    return text


def read_login_flow_guide(*, max_chars: int = 24_000) -> str:
    """兼容：等同 read_repo_skill('game_launch_ocr')。"""
    return read_repo_skill("game_launch_ocr", max_chars=max_chars)
