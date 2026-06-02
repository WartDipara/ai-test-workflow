"""将本轮用户侧输入与 LLM 新增消息格式化为可打印的控制台文本。"""

from __future__ import annotations

import re
from typing import Any

from pydantic_ai.messages import (
    BinaryImage,
    ModelMessage,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def format_user_parts_for_console(
    parts: list[str | BinaryImage],
    *,
    max_chars_per_text: int = 20000,
) -> str:
    """折叠大段 Base64，避免刷屏；多模态图只打字节长度。"""
    blocks: list[str] = []
    for i, p in enumerate(parts):
        if isinstance(p, BinaryImage):
            n = len(p.data) if p.data else 0
            blocks.append(f"--- 片段[{i}] BinaryImage | media_type={p.media_type} | bytes={n} ---")
        elif isinstance(p, str):
            s = p
            if "BASE64_BEGIN" in s and "BASE64_END" in s:
                s = re.sub(
                    r"BASE64_BEGIN\n[\s\S]*?\nBASE64_END",
                    "<BASE64_BEGIN … BASE64_END 已折叠（PNG Base64）>",
                    s,
                    count=1,
                )
            if len(s) > max_chars_per_text:
                s = s[:max_chars_per_text] + f"\n…[截断，原长 {len(p)} 字符]"
            blocks.append(f"--- 片段[{i}] 文本 ---\n{s}")
        else:
            blocks.append(f"--- 片段[{i}] {type(p).__name__} ---\n{repr(p)[:800]}")
    return "\n".join(blocks)


def _format_tool_args(args: Any, *, limit: int) -> str:
    text = args if isinstance(args, str) else repr(args)
    if len(text) > limit:
        return text[:limit] + f"... [截断，原长 {len(text)}]"
    return text


def format_new_llm_messages(
    messages: list[ModelMessage],
    *,
    max_tool_args: int = 2500,
) -> str:
    """从 `result.new_messages()` 提取思考链、最终文本、工具调用与工具返回。"""
    lines: list[str] = []
    for mi, msg in enumerate(messages):
        lines.append(f"========== 新消息[{mi}] 类型={type(msg).__name__} ==========")
        parts = getattr(msg, "parts", None)
        if not parts:
            lines.append("  (无 parts)")
            continue
        for pi, part in enumerate(parts):
            head = f"  [{pi}] {type(part).__name__}"
            if isinstance(part, ThinkingPart):
                body = part.content or ""
                lines.append(f"{head} | id={part.id!r}\n{body}")
            elif isinstance(part, TextPart):
                body = part.content or ""
                if len(body) > 12000:
                    body = body[:12000] + f"\n…[截断，原长 {len(part.content or '')}]"
                lines.append(f"{head}\n{body}")
            elif isinstance(part, ToolCallPart):
                args = _format_tool_args(part.args, limit=max_tool_args)
                lines.append(
                    f"{head} | tool={part.tool_name!r} | tool_call_id={part.tool_call_id!r}\n    args={args}",
                )
            elif isinstance(part, ToolReturnPart):
                ret = part.content
                rtxt = repr(ret) if ret is not None else ""
                if len(rtxt) > 4000:
                    rtxt = rtxt[:4000] + "..."
                lines.append(f"{head} | tool_name={part.tool_name!r}\n    return={rtxt}")
            elif isinstance(part, UserPromptPart):
                c = part.content
                r = repr(c)
                if len(r) > 10000:
                    r = r[:10000] + "..."
                lines.append(f"{head} | content={r}")
            else:
                lines.append(f"{head} | {repr(part)[:1200]}")
    return "\n".join(lines)
