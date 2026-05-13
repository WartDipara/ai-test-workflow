"""将截图（本地路径或 http(s) URL）转为可写入纯文本消息的 Base64 块。"""

from __future__ import annotations

import asyncio
import base64
import logging
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

logger = logging.getLogger(__name__)

_CAPTION = (
    "【image_data：当前游戏屏幕截图】\n"
    "说明：以下为 PNG 图像原始字节的 Base64 编码（UTF-8 可打印 ASCII）。"
    "请将 BASE64_BEGIN 与 BASE64_END 之间的内容解码为二进制 PNG 后再做视觉理解；"
    "不要逐字复述整段 Base64。\n"
    "MIME: image/png\n"
    "BASE64_BEGIN\n"
)

_CAPTION_END = "\nBASE64_END\n"


async def fetch_image_bytes(path_or_url: str) -> bytes:
    """本地路径用磁盘读取；http(s) 用 httpx GET（与「先 image_url 再拉取」等价，但不经由模型侧 image_url 字段）。"""

    if path_or_url.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            r = await client.get(path_or_url)
            r.raise_for_status()
            return r.content
    p = Path(path_or_url)
    if not p.is_file():
        raise FileNotFoundError(f"截图文件不存在: {p}")
    return p.read_bytes()


def resize_png_bytes(data: bytes, max_edge: int) -> bytes:
    """将 PNG 缩放到最长边不超过 max_edge，减轻纯文本模式下的 token 压力。"""

    im = Image.open(BytesIO(data))
    im = im.convert("RGBA") if im.mode not in ("RGB", "RGBA") else im
    im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    buf = BytesIO()
    im.save(buf, format="PNG", optimize=True)
    out = buf.getvalue()
    logger.debug("resize_png: in=%d bytes out=%d bytes", len(data), len(out))
    return out


async def build_screenshot_as_text_base64(
    path_or_url: str,
    *,
    max_edge: int,
) -> str:
    raw = await fetch_image_bytes(path_or_url)
    small = await asyncio.to_thread(resize_png_bytes, raw, max_edge)
    b64 = base64.b64encode(small).decode("ascii")
    return f"{_CAPTION}{b64}{_CAPTION_END}"
