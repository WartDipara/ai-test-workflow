from __future__ import annotations

from typing import Any, Literal

from game_agent.models.settings import DeepSeekSection

ReasoningEffort = Literal["high", "max"]

SUPPORTED_REASONING_EFFORT: frozenset[str] = frozenset({"high", "max"})

# strict Tool Calls 须使用 Beta 端点
DEEPSEEK_OFFICIAL_BASE = "https://api.deepseek.com"
DEEPSEEK_BETA_BASE = "https://api.deepseek.com/beta"

# 思考模式下不支持（设置了也不会生效，但去掉可避免部分 SDK 误传）
UNSUPPORTED_WHEN_THINKING = (
    "temperature",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
)


def normalize_reasoning_effort(effort: str) -> ReasoningEffort:
    e = (effort or "high").strip().lower()
    if e in SUPPORTED_REASONING_EFFORT:
        return e  # type: ignore[return-value]
    return "high"


def resolve_deepseek_base_url(base_url: str, *, tool_calls_strict: bool) -> str:
    """strict 模式见 Tool Calls 文档，须 base_url=https://api.deepseek.com/beta。"""
    base = (base_url or DEEPSEEK_OFFICIAL_BASE).rstrip("/")
    if tool_calls_strict:
        if base.endswith("/beta"):
            return base
        if base == DEEPSEEK_OFFICIAL_BASE or base.endswith("api.deepseek.com"):
            return DEEPSEEK_BETA_BASE
        return f"{base}/beta"
    return base


def apply_deepseek_thinking_settings(
    settings: dict[str, Any],
    deepseek: DeepSeekSection,
) -> dict[str, Any]:
    """
    按官方文档注入思考模式参数；thinking=false 时不修改。

    与 Tool Calls 同用：reasoning_effort + extra_body.thinking.type=enabled。
    思考模式下发生 tool_calls 时，后续轮次须回传 reasoning_content（由模型 profile 处理）。
    """
    if not deepseek.thinking:
        return settings

    effort = normalize_reasoning_effort(deepseek.reasoning_effort)
    settings["openai_reasoning_effort"] = effort

    extra_body = settings.get("extra_body")
    if not isinstance(extra_body, dict):
        extra_body = {}
    extra_body.setdefault("thinking", {"type": "enabled"})
    # 部分网关仅认 body 内字段，与顶层 reasoning_effort 双写
    extra_body.setdefault("reasoning_effort", effort)
    settings["extra_body"] = extra_body

    for key in UNSUPPORTED_WHEN_THINKING:
        settings.pop(key, None)

    return settings


def tool_round_requires_reasoning_in_context(has_tool_calls: bool) -> bool:
    """
    思考模式 + 发生过 tool_calls 时，assistant 必须携带 reasoning_content。
    见 https://api-docs.deepseek.com/zh-cn/guides/tool_calls
    """
    return has_tool_calls
