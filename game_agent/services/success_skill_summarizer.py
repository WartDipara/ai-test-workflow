from __future__ import annotations

import logging
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from game_agent.models.settings import AppConfig
from game_agent.services.learned_skill_store import write_skill_markdown
from game_agent.services.llm_service import build_llm_model
from game_agent.services.llm_transcript import format_new_llm_messages

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM = """You compress a successful OCR+AI game-login executor run into a short reusable skill note.

Rules:
1. Markdown body only; no preamble; do not wrap the whole output in a code fence.
2. First line must be `# Learned skill: <short title under ~10 words>`.
3. Keep under ~800 English words; use short lists: stage ID order, key OCR phrases, buttons per stage, pitfalls.
4. Contrast `skills/game-launch-ocr/SKILL.md` generic stages with this game's deltas; do not copy large chunks.
5. Do not invent steps not in the transcript; no API keys, tokens, or account secrets.
6. Do not paste full OCR dumps; keywords only."""


def _strip_fenced_markdown(text: str) -> str:
    t = (text or "").strip()
    m = re.match(r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*$", t)
    if m:
        return m.group(1).strip()
    return t


async def write_skill_from_success_run(
    app_config: AppConfig,
    history: list[ModelMessage],
    *,
    task_label: str,
    final_summary: str,
    rounds_used: int,
    artifact_run_dir: str,
) -> Path | None:
    """
    用主 LLM 将成功 run 的对话压成短技能 Markdown，写入 experiences/agent_skills/。
    失败时记录日志并返回 None（不影响主流程成功状态）。
    """
    transcript = format_new_llm_messages(history, max_tool_args=900)
    max_transcript = 28_000
    if len(transcript) > max_transcript:
        transcript = transcript[:max_transcript] + "\n…[对话转写已截断]"

    user_block = f"""Task: {task_label!r}
Rounds completed: {rounds_used}
Artifact dir: {artifact_run_dir!r}
Final report_flow_done summary:
{final_summary.strip()[:3000]}

--- Full conversation transcript (tools + returns) ---
{transcript}
"""

    try:
        agent = Agent(
            build_llm_model(app_config.llm, deepseek=app_config.deepseek),
            system_prompt=_SUMMARY_SYSTEM,
            output_type=str,
        )
        result = await agent.run(user_block)
        body = _strip_fenced_markdown(result.output or "")
        if not body or len(body) < 20:
            logger.warning("已学技能生成结果过短，跳过写入")
            return None
    except Exception:
        logger.exception("已学技能：LLM 总结失败")
        return None

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    h = hashlib.sha256(task_label.encode("utf-8")).hexdigest()[:10]
    basename = f"skill_{stamp}_{h}.md"
    try:
        path = write_skill_markdown(basename=basename, body=body)
    except ValueError:
        basename = f"skill_{stamp}_fallback.md"
        path = write_skill_markdown(basename=basename, body=body)
    logger.info("已写入已学技能: %s", path)
    return path
