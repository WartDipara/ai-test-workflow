from __future__ import annotations

import json
from enum import IntEnum
from typing import Any


class VisionToolErrorCode(IntEnum):
    """工具是否成功完成多模态调用（非业务「是否进游戏」）。"""

    OK = 0
    NO_MULTIMODAL = 1
    API_ERROR = 2
    PARSE_ERROR = 3
    OCR_FAILED = 4


def format_vision_tool_response(
    *,
    error_code: VisionToolErrorCode | int,
    error_message: str = "",
    data: dict[str, Any] | None = None,
) -> str:
    """
    主脑工具统一 JSON 回调格式。
    completed=true 表示多模态/判定流程已跑完且 payload 可用（errorCode=0）。
    """
    code = int(error_code)
    payload = {
        "errorCode": code,
        "errorMessage": (error_message or "").strip(),
        "completed": code == VisionToolErrorCode.OK,
        "data": data or {},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
