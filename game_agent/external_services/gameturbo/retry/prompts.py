from __future__ import annotations

from functools import lru_cache

from game_agent.paths import REPO_ROOT
from game_agent.services.skill_catalog import read_repo_skill

_BASELINE_SAMPLE_LOGS = (
    REPO_ROOT / "正常的网络流量情况.log",
    REPO_ROOT / "另一段正常的网络加速日志.log",
)


@lru_cache(maxsize=1)
def load_plugin_accel_log_skill() -> str:
    """Load plugin accel log baseline skill for AI prompt injection."""
    text = read_repo_skill("plugin_accel_log", max_chars=96_000)
    if text.startswith("[missing]") or text.startswith("Unknown skill_id"):
        return ""
    return text


def plugin_accel_log_prompt_block() -> str:
    """Return baseline guidance block for LLM prompts."""
    skill = load_plugin_accel_log_skill().strip()
    found = [str(p) for p in _BASELINE_SAMPLE_LOGS if p.is_file()]
    sample = (
        "Baseline sample logs: " + ", ".join(found)
        if found
        else "(no healthy .log samples in repo; SKILL text only)"
    )
    if not skill:
        return (
            "[Plugin accel log baseline] SKILL file missing; distinguish tunnel reconnect vs real faults.\n"
            + sample
        )
    return f"[Plugin accel log baseline — mandatory]\n{sample}\n\n{skill}\n"
