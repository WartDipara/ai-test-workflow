from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from game_agent.services.llm_adapters.base import BaseModelAdapter


class GenericOpenAIAdapter(BaseModelAdapter):
    """
    通用 OpenAI 兼容模型适配器。
    不做任何特殊魔改，直接返回 OpenAIChatModel。
    """

    def build_model(self) -> Model:
        provider = OpenAIProvider(
            base_url=self.llm_config.base_url,
            api_key=self.llm_config.api_key,
        )
        return OpenAIChatModel(self.llm_config.model_name, provider=provider)
