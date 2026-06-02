from __future__ import annotations

from pydantic_ai.models import Model

from game_agent.models.settings import LLMSection
from game_agent.services.llm_adapters.base import BaseModelAdapter
from game_agent.services.llm_adapters.deepseek import DeepSeekAdapter
from game_agent.services.llm_adapters.openai import GenericOpenAIAdapter
from game_agent.services.llm_adapters.qwen import QwenAdapter


def build_llm_model(llm: LLMSection) -> Model:
    """
    根据配置中的 model_name 动态选择适配器，构造兼容模型。
    """
    model_name = llm.model_name.lower()

    # 简单路由逻辑：根据模型名称分发到不同的 Adapter
    adapter: BaseModelAdapter
    if (
        "deepseek" in model_name
        and ("v4-flash" in model_name or "v4-pro" in model_name)
        and not llm.deepseek_litellm_compat
    ):
        adapter = DeepSeekAdapter(llm)
    elif "qwen" in model_name:
        adapter = QwenAdapter(llm)
    else:
        adapter = GenericOpenAIAdapter(llm)

    return adapter.build_model()
