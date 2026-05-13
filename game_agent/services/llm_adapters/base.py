from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic_ai.models import Model

from game_agent.models.settings import LLMSection


class BaseModelAdapter(ABC):
    """
    模型适配器基类。
    用于根据不同模型厂商/系列的特性，定制化 Pydantic-AI 的 Model 实例或请求参数。
    """

    def __init__(self, llm_config: LLMSection):
        self.llm_config = llm_config
        if not self.llm_config.api_key or self.llm_config.api_key.startswith("${"):
            raise ValueError(
                "llm.api_key 无效或未展开：请在 YAML 中填写或通过 ${ENV} 注入环境变量",
            )

    @abstractmethod
    def build_model(self) -> Model:
        """构造并返回 Pydantic-AI 的 Model 实例"""
        pass
