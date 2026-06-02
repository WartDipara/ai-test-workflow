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

_SUMMARY_SYSTEM = """你是「经验压缩器」，为同一套 OCR+AI 游戏登录执行者编写可复用的短技能笔记。

输出要求（必须遵守）：
1. 只输出 Markdown 正文，不要前言后语、不要用 markdown 代码围栏包裹全文。
2. 第一行必须是形如 `# 已学技能：<10 字内标题>` 的一级标题。
3. 正文控制在 1200 汉字以内；用短列表写：阶段 ID 顺序、关键 OCR 词、各阶段按钮、易错点。
4. 可对照 `skills/game-launch-ocr/SKILL.md` 的通用阶段模型写「本游戏差异」，不要照抄大段。
5. 不得编造对话里未出现的操作；不要输出任何 API key、token、账号。
6. 不要粘贴整段 OCR，只提关键词即可。"""


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

    user_block = f"""任务标识: {task_label!r}
完成轮数: {rounds_used}
artifact 目录名: {artifact_run_dir!r}
最终 report_flow_done 摘要:
{final_summary.strip()[:3000]}

--- 本轮完整对话转写（工具调用与返回）---
{transcript}
"""

    try:
        agent = Agent(
            build_llm_model(app_config.llm),
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
