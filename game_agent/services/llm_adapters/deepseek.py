from __future__ import annotations

import logging

import openai.types.chat as chat
from pydantic_ai.messages import ModelResponse, ThinkingPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from game_agent.services.llm_adapters.base import BaseModelAdapter

logger = logging.getLogger(__name__)


class DeepSeekThinkingModel(OpenAIChatModel):
    """
    深度定制的 DeepSeek 模型类，重写相关方法以完美适配思考模式（Thinking Mode）。
    """

    def prepare_request(
        self,
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[ModelSettings | None, ModelRequestParameters]:
        settings = dict(model_settings or {})

        # 注入 DeepSeek 思考模式特有参数
        # 详见：https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
        if "openai_reasoning_effort" not in settings:
            settings["openai_reasoning_effort"] = "high"

        extra_body = settings.get("extra_body")
        if extra_body is None:
            extra_body = {}

        if isinstance(extra_body, dict):
            # 开启 thinking mode
            extra_body.setdefault("thinking", {"type": "enabled"})
            settings["extra_body"] = extra_body

        return super().prepare_request(settings, model_request_parameters)

    def _process_thinking(self, message: chat.ChatCompletionMessage) -> list[ThinkingPart] | None:
        """
        DeepSeek 兜底提取 reasoning_content。
        某些 SDK / 网关兼容层下，reasoning_content 可能只出现在 model_extra，
        这里显式兜底抓取，确保上层能看到思考内容。
        """
        items = super()._process_thinking(message) or []
        if items:
            # 打印完整思考内容（不截断）
            full = "\n\n".join((p.content or "") for p in items).strip()
            logger.info("DeepSeek thinking 已解析: parts=%d\n%s", len(items), full)
            return items

        extra = getattr(message, "model_extra", None) or {}
        if not isinstance(extra, dict):
            logger.info("DeepSeek thinking 缺失: message.model_extra 不是 dict")
            return None

        reasoning = extra.get("reasoning_content") or extra.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            logger.info("DeepSeek thinking 通过 model_extra 兜底提取成功:\n%s", reasoning)
            return [ThinkingPart(id="reasoning_content", content=reasoning, provider_name=self.system)]
        logger.info("DeepSeek thinking 缺失: 响应未含 reasoning_content/reasoning")
        return None

    def _map_model_response(self, message: ModelResponse) -> chat.ChatCompletionMessageParam | None:
        """
        显式重写响应映射逻辑，确保 reasoning_content 被完整回传给 API。
        
        根据 DeepSeek 文档：在进行了工具调用的轮次中，后续所有交互必须回传 reasoning_content。
        虽然 Pydantic-AI 底层（_MapModelResponseContext）目前默认会根据
        ThinkingPart(id='reasoning_content') 自动拼装这个字段，但为了遵循文档严格要求、
        防止未来框架默认行为变更，并保持代码的显式易读，我们在这里显式处理。
        """
        message_param = super()._map_model_response(message)
        if message_param is None:
            return None
            
        # 遍历消息的所有部分，寻找思考内容
        for part in message.parts:
            if isinstance(part, ThinkingPart) and part.content:
                # 强制将思考内容注入到 assistant 消息的 reasoning_content 字段中
                # 这样在多轮对话（特别是 tool_calls 之间）API 就能接收到完整的思维链上下文
                message_param["reasoning_content"] = part.content  # type: ignore
                break
                
        return message_param


class DeepSeekAdapter(BaseModelAdapter):
    """
    DeepSeek 模型适配器。
    针对 deepseek-v4-flash / deepseek-v4-pro 等支持思考模式的模型，
    返回定制的 DeepSeekThinkingModel。
    """

    def build_model(self) -> Model:
        provider = OpenAIProvider(
            base_url=self.llm_config.base_url,
            api_key=self.llm_config.api_key,
        )
        return DeepSeekThinkingModel(self.llm_config.model_name, provider=provider)
