from __future__ import annotations

import logging

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from game_agent.services.llm_adapters.base import BaseModelAdapter

logger = logging.getLogger(__name__)


class QwenMultimodalModel(OpenAIChatModel):
    """
    Qwen 多模态模型类。
    专门处理 Qwen 的多模态信息结构。
    如果 Qwen 需要对图片格式、特定的消息结构做特殊处理，可以在这里重写。
    目前底层使用 OpenAI 兼容格式。
    """
    pass


class QwenAdapter(BaseModelAdapter):
    """
    Qwen 模型适配器。
    用于返回支持多模态（视觉）的 Qwen 模型实例。
    """

    def build_model(self) -> Model:
        provider = OpenAIProvider(
            base_url=self.llm_config.base_url,
            api_key=self.llm_config.api_key,
        )
        return QwenMultimodalModel(self.llm_config.model_name, provider=provider)
