"""启动前检测：当前多模态 LLM 端点是否接受 OpenAI 风格的 image_url 多模态消息。"""

from __future__ import annotations

import logging

from openai import APIStatusError, AsyncOpenAI, BadRequestError

from game_agent.models.settings import LLMSection

logger = logging.getLogger(__name__)

# Qwen 等多模态网关可能拒绝宽高 <= 10 的图片，因此不要使用 1x1 探针图。
_MIN_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAI0lEQVR4nGP8//8/AymAiSTVDKMaiANMRKqDg1ENxACSQwkAVW0DHeN02ZEAAAAASUVORK5CYII="
)


async def probe_multimodal_support(llm: LLMSection) -> str | None:
    base = llm.base_url.rstrip("/")
    client = AsyncOpenAI(base_url=base, api_key=llm.api_key)
    try:
        await client.chat.completions.create(
            model=llm.model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Reply with digit 1 only."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{_MIN_PNG_B64}"},
                        },
                    ],
                }
            ],
            max_tokens=8,
        )
        logger.info("Multimodal probe OK: API accepted image_url")
        return None
    except BadRequestError as e:
        body = getattr(e, "body", None) or str(e)
        snippet = str(body)[:1200]
        logger.error("Multimodal probe failed (BadRequest): %s", snippet)
        return _format_vision_failure(snippet)
    except APIStatusError as e:
        snippet = str(e)[:1200]
        logger.error("Multimodal probe failed (HTTP): %s", snippet)
        return _format_vision_failure(snippet)
    except Exception as e:
        logger.exception("Multimodal probe unexpected error")
        return f"Multimodal probe unexpected error: {e!s}"


async def probe_startup_for_llm(llm: LLMSection, llm_multimodal: LLMSection | None = None) -> str | None:
    """探测多模态模型是否正常工作。"""
    target_llm = llm_multimodal or llm
    return await probe_multimodal_support(target_llm)


def _format_vision_failure(api_detail: str) -> str:
    return (
        "[Startup check] Multimodal LLM config does not accept image_url / screenshots required by this agent.\n"
        "Executor rounds attach screenshots — use a vision-capable OpenAI-compatible model/endpoint, "
        "or confirm image support in vendor docs.\n"
        f"API detail: {api_detail}"
    )
