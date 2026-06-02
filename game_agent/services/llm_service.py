from __future__ import annotations

from pydantic_ai.models import Model

from game_agent.models.settings import DeepSeekSection, LLMSection, is_deepseek_model
from game_agent.services.llm_adapters.base import BaseModelAdapter
from game_agent.services.llm_adapters.deepseek import DeepSeekAdapter
from game_agent.services.llm_adapters.openai import GenericOpenAIAdapter
from game_agent.services.llm_adapters.qwen import QwenAdapter


def build_llm_model(
    llm: LLMSection,
    *,
    deepseek: DeepSeekSection | None = None,
) -> Model:
    """
    根据 model_name 选择适配器。
    DeepSeek 官方模型走 DeepSeekAdapter（参数来自独立 deepseek 配置段），其余走 Qwen / 通用 OpenAI。
    """
    model_name = llm.model_name.lower()
    ds = deepseek or DeepSeekSection()

    adapter: BaseModelAdapter
    if is_deepseek_model(model_name):
        adapter = DeepSeekAdapter(llm, ds)
    elif "qwen" in model_name:
        adapter = QwenAdapter(llm)
    else:
        adapter = GenericOpenAIAdapter(llm)

    return adapter.build_model()
