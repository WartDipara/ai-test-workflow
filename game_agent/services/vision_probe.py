"""启动前检测：当前 LLM 端点是否接受 OpenAI 风格的 image_url 多模态消息。"""

from __future__ import annotations

import logging

from openai import APIStatusError, AsyncOpenAI, BadRequestError

from game_agent.models.settings import LLMSection

logger = logging.getLogger(__name__)

# 1x1 PNG（透明），体积极小
_MIN_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


async def probe_multimodal_support(llm: LLMSection) -> str | None:
    """
    发送一条带 `image_url`（data URI）的 chat 请求。

    若 API 不支持多模态，通常返回 400（如 DeepSeek 文本接口报 unknown variant `image_url`）。

    Returns:
        None 表示通过；非空为简短错误说明（供日志与 RunState.note）。
    """
    base = llm.base_url.rstrip("/")
    client = AsyncOpenAI(base_url=base, api_key=llm.api_key)
    try:
        await client.chat.completions.create(
            model=llm.model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "只回复数字 1。"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{_MIN_PNG_B64}"},
                        },
                    ],
                }
            ],
            max_tokens=8,
        )
        logger.info("多模态探针通过：API 接受了 image_url 消息")
        return None
    except BadRequestError as e:
        body = getattr(e, "body", None) or str(e)
        snippet = str(body)[:1200]
        logger.error("多模态探针失败（BadRequest）: %s", snippet)
        return _format_vision_failure(snippet)
    except APIStatusError as e:
        snippet = str(e)[:1200]
        logger.error("多模态探针失败（HTTP）: %s", snippet)
        return _format_vision_failure(snippet)
    except Exception as e:
        logger.exception("多模态探针发生非预期异常")
        return f"多模态探针异常（非 400 类）: {e!s}"


async def probe_text_chat_only(llm: LLMSection) -> str | None:
    """仅文本连通性检查（用于 image_transport=text_base64，因不会发 image_url）。"""
    base = llm.base_url.rstrip("/")
    client = AsyncOpenAI(base_url=base, api_key=llm.api_key)
    try:
        await client.chat.completions.create(
            model=llm.model_name,
            messages=[{"role": "user", "content": "只回复数字 1。"}],
            max_tokens=4,
        )
        logger.info("文本探针通过（text_base64 模式不做 image_url 多模态探针）")
        return None
    except BadRequestError as e:
        body = getattr(e, "body", None) or str(e)
        return f"【启动检查】LLM 文本请求失败: {str(body)[:1200]}"
    except APIStatusError as e:
        return f"【启动检查】LLM HTTP 错误: {str(e)[:1200]}"
    except Exception as e:
        return f"【启动检查】LLM 探针异常: {e!s}"


async def probe_startup_for_llm(llm: LLMSection) -> str | None:
    """按 image_transport 选择探针：多模态端点走 image_url；纯文本嵌入图走文本 ping。"""
    if llm.image_transport == "text_base64":
        return await probe_text_chat_only(llm)
    return await probe_multimodal_support(llm)


def _format_vision_failure(api_detail: str) -> str:
    return (
        "【启动检查】当前 LLM 配置不支持本 Agent 所需的多模态输入（消息中含 image_url / 截图）。\n"
        "登录流程每轮都会附带截屏，请更换为支持视觉的 OpenAI 兼容模型与端点，"
        "或查阅厂商文档确认该 model 是否支持图片。\n"
        f"API 返回摘要: {api_detail}"
    )
