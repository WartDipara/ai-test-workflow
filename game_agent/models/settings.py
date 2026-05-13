from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LLMSection(BaseModel):
    """LLM API 配置：全部来自配置文件，禁止在业务代码中写死。"""

    base_url: str = Field(..., description="OpenAI 兼容 API base URL")
    api_key: str = Field(..., description="API Key，可配合 YAML 中的 ${ENV} 由加载器展开")
    model_name: str = Field(..., description="模型名称，如 gpt-4o、deepseek-chat")
    image_transport: Literal["openai_multimodal", "text_base64"] = Field(
        "openai_multimodal",
        description=(
            "openai_multimodal：OpenAI Chat 多模态（image_url/data URI）。"
            "text_base64：截图 Base64 写入纯文本并附说明（适配拒绝 image_url 的网关）。"
        ),
    )
    skip_vision_probe: bool = Field(
        False,
        description="为 true 时跳过启动时的多模态探针（仅调试用；正式跑登录需 vision）",
    )


class AdbSection(BaseModel):
    serial: str | None = Field(None, description="adb -s；为空则省略")


class GameSection(BaseModel):
    package_name: str = Field(..., description="游戏包名")
    activity: str | None = Field(
        None,
        description="完整组件名；为空则使用 monkey 启动包",
    )


class CredentialsSection(BaseModel):
    file_path: Path = Field(..., description="账号密码 YAML 路径")


class AgentSection(BaseModel):
    max_rounds: int = Field(30, ge=1, le=200)
    artifacts_dir: Path = Field(Path("./artifacts"))
    screenshot_max_edge: int = Field(
        768,
        ge=256,
        le=2048,
        description="image_transport=text_base64 时，将截图缩放到最长边不超过该值以控制 token",
    )


class LoggingSection(BaseModel):
    level: str = Field("INFO")


class AppConfig(BaseModel):
    """根配置，对应一份 YAML。"""

    llm: LLMSection
    adb: AdbSection = Field(default_factory=AdbSection)
    game: GameSection
    credentials: CredentialsSection
    agent: AgentSection = Field(default_factory=AgentSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
