from __future__ import annotations

from functools import lru_cache

from game_agent.paths import REPO_ROOT

_GAMETURBO_LOG_BASELINE_SKILL = (
    REPO_ROOT / "skills" / "gameturbo-log-baseline" / "SKILL.md"
)
_BASELINE_SAMPLE_LOGS = (
    REPO_ROOT / "正常的网络流量情况.log",
    REPO_ROOT / "另一段正常的网络加速日志.log",
)


@lru_cache(maxsize=1)
def load_gameturbo_log_baseline_skill() -> str:
    """加载 GameTurbo 日志正常基线技能（供 AI prompt 注入）。"""
    if _GAMETURBO_LOG_BASELINE_SKILL.is_file():
        return _GAMETURBO_LOG_BASELINE_SKILL.read_text(encoding="utf-8")
    return ""


def gameturbo_log_baseline_prompt_block() -> str:
    """返回可拼进 LLM prompt 的基线指引块。"""
    skill = load_gameturbo_log_baseline_skill().strip()
    found = [str(p) for p in _BASELINE_SAMPLE_LOGS if p.is_file()]
    sample = (
        "基准样本: " + ", ".join(found)
        if found
        else "（仓库内健康样本 .log 未找到，仅使用 SKILL 条文）"
    )
    if not skill:
        return (
            "【GameTurbo 日志基线】技能文件缺失，请谨慎区分 tunnel 重连与真实故障。\n"
            + sample
        )
    return f"【GameTurbo 日志正常基线 — 须遵守】\n{sample}\n\n{skill}\n"
