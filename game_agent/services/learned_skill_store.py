from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from game_agent.paths import REPO_ROOT

logger = logging.getLogger(__name__)

EXPERIENCES_DIR = REPO_ROOT / "experiences"
AGENT_SKILLS_DIR = EXPERIENCES_DIR / "agent_skills"
TRACKER_FILE = AGENT_SKILLS_DIR / "learned_skills_tracker.json"
MAX_SKILLS_PER_PACKAGE = 3


def agent_skills_dir() -> Path:
    return AGENT_SKILLS_DIR


def ensure_skills_dir() -> None:
    AGENT_SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _tracker_path() -> Path:
    ensure_skills_dir()
    return TRACKER_FILE


def _load_tracker() -> dict[str, list[str]]:
    """载入追踪 JSON：{ package_name: [skill_filename.md, ...] }"""
    path = _tracker_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("技能追踪文件损坏，重置: %s", e)
        return {}


def _save_tracker(data: dict[str, list[str]]) -> None:
    path = _tracker_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def can_learn_package(package_name: str) -> bool:
    """该包是否还能继续学习（< MAX_SKILLS_PER_PACKAGE）。"""
    tracker = _load_tracker()
    return len(tracker.get(package_name, [])) < MAX_SKILLS_PER_PACKAGE


def record_learned_package(package_name: str, skill_basename: str) -> None:
    """记录该包已学习一个技能。"""
    tracker = _load_tracker()
    entry = tracker.setdefault(package_name, [])
    if skill_basename not in entry:
        entry.append(skill_basename)
    _save_tracker(tracker)


def latest_skill_for_package(package_name: str) -> Path | None:
    """返回该包最新一条技能文件路径，若无则 None。"""
    tracker = _load_tracker()
    filenames = tracker.get(package_name)
    if not filenames:
        return None
    latest = filenames[-1]
    path = AGENT_SKILLS_DIR / latest
    return path if path.is_file() else None


def safe_skill_basename(name: str) -> str | None:
    """仅允许单层 *.md 文件名，防路径穿越。"""
    raw = (name or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        return None
    base = Path(raw).name
    if base in (".", "..") or not base.lower().endswith(".md"):
        return None
    if len(base) > 180:
        return None
    if any(ord(c) < 32 for c in base):
        return None
    return base


def list_skill_files(*, limit: int = 20) -> list[Path]:
    ensure_skills_dir()
    files = [p for p in AGENT_SKILLS_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".md"]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[: max(1, min(limit, 50))]


def _preview_first_line(path: Path, *, max_chars: int = 120) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(read failed: {e})"
    for line in text.splitlines():
        s = line.strip()
        if s in ("---", ""):
            continue
        if s.startswith("#"):
            return (s[:max_chars] + "…") if len(s) > max_chars else s
    body = text.strip().replace("\n", " ")
    return (body[:max_chars] + "…") if len(body) > max_chars else body or "(empty)"


def format_skill_list_for_tool(*, limit: int = 15) -> str:
    files = list_skill_files(limit=limit)
    if not files:
        return "(No learned skill files yet; generated after successful run with persist_learned_skill_on_success.)"
    lines: list[str] = []
    for p in files:
        ts = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"- {p.name} | mtime={ts} | {_preview_first_line(p)}")
    return "\n".join(lines)


def read_skill_file(basename: str, *, max_bytes: int = 48_000) -> str:
    safe = safe_skill_basename(basename)
    if not safe:
        return "Error: invalid filename. Pass only *.md under experiences/agent_skills/, e.g. skill_20260215_xxx.md"
    path = AGENT_SKILLS_DIR / safe
    if not path.is_file():
        return f"Error: file not found {safe}. Call list_learned_skills first."
    try:
        data = path.read_bytes()
    except OSError as e:
        return f"Read failed: {e}"
    raw = data[:max_bytes]
    text = raw.decode("utf-8", errors="replace")
    if len(data) > max_bytes:
        text += f"\n…[truncated at {max_bytes} bytes]"
    return f"=== {safe} ===\n{text}"


def write_skill_markdown(*, basename: str, body: str) -> Path:
    ensure_skills_dir()
    safe = safe_skill_basename(basename)
    if not safe:
        raise ValueError(f"非法技能文件名: {basename!r}")
    path = AGENT_SKILLS_DIR / safe
    path.write_text(body.strip() + "\n", encoding="utf-8")
    return path
