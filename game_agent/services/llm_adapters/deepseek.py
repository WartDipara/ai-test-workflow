from __future__ import annotations

import logging
from dataclasses import replace

import openai.types.chat as chat
from pydantic_ai.messages import ModelResponse, ThinkingPart, ToolCallPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from game_agent.models.settings import DeepSeekSection, LLMSection
from game_agent.services.llm_adapters.base import BaseModelAdapter
from game_agent.services.llm_adapters.deepseek_protocol import (
    apply_deepseek_thinking_settings,
    resolve_deepseek_base_url,
    tool_round_requires_reasoning_in_context,
)

logger = logging.getLogger(__name__)

# Tool Calls 行为说明（实现依赖 Pydantic-AI DeepSeek profile + 本类回传 reasoning_content）
_DEEPSEEK_TOOL_CALLS_DOC = "https://api-docs.deepseek.com/zh-cn/guides/tool_calls"
_REASONING_FIELD = "reasoning_content"


def _response_has_tool_calls(response: ModelResponse) -> bool:
    return any(isinstance(p, ToolCallPart) for p in response.parts)


def _response_thinking_text(response: ModelResponse) -> str | None:
    chunks: list[str] = []
    for part in response.parts:
        if isinstance(part, ThinkingPart):
            chunks.append(part.content or "")
    if not chunks:
        return None
    return "\n\n".join(chunks)


class DeepSeekChatModel(OpenAIChatModel):
    """
    DeepSeek 官方 Chat Completions（OpenAI 兼容）。

    - 思考模式：reasoning_effort=high|max + thinking.type=enabled
    - Tool Calls：仅 DeepSeek；思考模式下 tool 轮次须回传 reasoning_content（见官方文档）
    """

    def __init__(
        self,
        model_name: str,
        *,
        provider: OpenAIProvider,
        profile,
        deepseek: DeepSeekSection,
    ) -> None:
        super().__init__(model_name, provider=provider, profile=profile)
        self._deepseek = deepseek

    def prepare_request(
        self,
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[ModelSettings | None, ModelRequestParameters]:
        settings = dict(model_settings or {})
        settings = apply_deepseek_thinking_settings(settings, self._deepseek)

        if model_request_parameters.function_tools:
            if self._deepseek.thinking:
                logger.debug(
                    "DeepSeek 思考模式 + Tool Calls（%d 个工具）；"
                    "assistant 含 tool_calls 时将回传 reasoning_content",
                    len(model_request_parameters.function_tools),
                    extra={"doc": _DEEPSEEK_TOOL_CALLS_DOC},
                )
            if self._deepseek.tool_calls_strict:
                logger.debug(
                    "DeepSeek strict Tool Calls 已开启，请确保 base_url 为 /beta 且工具 schema 符合官方 strict 规范",
                    extra={"doc": _DEEPSEEK_TOOL_CALLS_DOC},
                )

        return super().prepare_request(settings, model_request_parameters)

    def _process_thinking(self, message: chat.ChatCompletionMessage) -> list[ThinkingPart] | None:
        """解析 reasoning_content（思考模式与 Tool Calls 均可能返回）。"""
        items = super()._process_thinking(message) or []
        if items:
            full = "\n\n".join((p.content or "") for p in items).strip()
            logger.info("DeepSeek thinking 已解析: parts=%d\n%s", len(items), full)
            return items

        reasoning = getattr(message, _REASONING_FIELD, None)
        extra = getattr(message, "model_extra", None) or {}
        if not isinstance(reasoning, str) and isinstance(extra, dict):
            reasoning = extra.get(_REASONING_FIELD) or extra.get("reasoning")

        if isinstance(reasoning, str) and reasoning.strip():
            logger.info("DeepSeek thinking 通过 message / model_extra 兜底提取成功")
            return [
                ThinkingPart(
                    id=_REASONING_FIELD,
                    content=reasoning,
                    provider_name=self.system,
                ),
            ]

        # 思考模式 + tool_calls 时 API 可能返回空 content 与空 reasoning；仍须落库以便回传
        if self._deepseek.thinking and message.tool_calls:
            logger.debug(
                "DeepSeek 响应含 tool_calls 但无 reasoning 文本，写入空 %s 以便后续轮次回传",
                _REASONING_FIELD,
            )
            return [
                ThinkingPart(
                    id=_REASONING_FIELD,
                    content="",
                    provider_name=self.system,
                ),
            ]
        return None

    def _process_response(self, response: chat.ChatCompletion | str) -> ModelResponse:
        model_response = super()._process_response(response)
        if not self._deepseek.thinking or not _response_has_tool_calls(model_response):
            return model_response
        if _response_thinking_text(model_response) is not None:
            return model_response

        logger.warning(
            "DeepSeek 响应含 tool_calls 但未解析出 %s，已补空思维链以防后续 Tool 轮 400",
            _REASONING_FIELD,
        )
        return replace(
            model_response,
            parts=[
                ThinkingPart(
                    id=_REASONING_FIELD,
                    content="",
                    provider_name=self.system,
                ),
                *model_response.parts,
            ],
        )

    def _attach_reasoning_to_message_param(
        self,
        message: ModelResponse,
        message_param: chat.ChatCompletionMessageParam,
    ) -> None:
        """将 ThinkingPart 写入 assistant.reasoning_content（允许空串）。"""
        for part in message.parts:
            if isinstance(part, ThinkingPart):
                message_param[_REASONING_FIELD] = part.content or ""  # type: ignore[literal-required]
                return
        text = _response_thinking_text(message)
        if text is not None:
            message_param[_REASONING_FIELD] = text  # type: ignore[literal-required]

    def _map_model_response(self, message: ModelResponse) -> chat.ChatCompletionMessageParam | None:
        """
        将 ThinkingPart 写回 assistant.reasoning_content。

        思考模式下若本轮含 tool_calls，未回传 reasoning_content 时 API 会 400；
        见 https://api-docs.deepseek.com/zh-cn/guides/tool_calls
        """
        message_param = super()._map_model_response(message)
        if message_param is None:
            return None

        has_tool_calls = _response_has_tool_calls(message)
        if not self._deepseek.thinking and not tool_round_requires_reasoning_in_context(
            has_tool_calls,
        ):
            return message_param

        self._attach_reasoning_to_message_param(message, message_param)

        if has_tool_calls and self._deepseek.thinking:
            if _REASONING_FIELD not in message_param:  # type: ignore[operator]
                message_param[_REASONING_FIELD] = ""  # type: ignore[literal-required]
                logger.warning(
                    "DeepSeek 历史 assistant 含 tool_calls 但无 %s，已补空串回传（避免 400）",
                    _REASONING_FIELD,
                )

        return message_param


class DeepSeekAdapter(BaseModelAdapter):
    """
    DeepSeek 官方 API 专用适配器（https://api.deepseek.com）。

    其它厂商模型请走 GenericOpenAIAdapter / QwenAdapter，勿复用本类。
    """

    def __init__(self, llm_config: LLMSection, deepseek: DeepSeekSection) -> None:
        super().__init__(llm_config)
        self._deepseek = deepseek

    def build_model(self) -> Model:
        model_name = self.llm_config.model_name
        base_url = resolve_deepseek_base_url(
            self.llm_config.base_url,
            tool_calls_strict=self._deepseek.tool_calls_strict,
        )

        # 官方 profile：reasoning_content 字段、tool 轮次回传思维链、v4 禁用 tool_choice=required
        profile = DeepSeekProvider.model_profile(model_name)

        provider = OpenAIProvider(
            base_url=base_url,
            api_key=self.llm_config.api_key,
        )
        return DeepSeekChatModel(
            model_name,
            provider=provider,
            profile=profile,
            deepseek=self._deepseek,
        )
