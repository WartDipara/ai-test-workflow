"""Chunked full multimodal output logging (process.log, no truncation)."""

from __future__ import annotations

import json
import logging
from typing import Any

_CHUNK = 8000


def log_full_text(
    logger: logging.Logger,
    prefix: str,
    text: str,
    *,
    level: int = logging.INFO,
) -> None:
    """Write full text in chunks so handlers do not truncate a single record."""
    body = text or ""
    logger.log(level, "%s full_output_begin len=%d", prefix, len(body))
    if not body:
        logger.log(level, "%s full_output_end", prefix)
        return
    for offset in range(0, len(body), _CHUNK):
        logger.log(level, "%s | %s", prefix, body[offset : offset + _CHUNK])
    logger.log(level, "%s full_output_end", prefix)


def log_vision_json(
    logger: logging.Logger,
    prefix: str,
    payload: dict[str, Any] | str,
    *,
    summary: str = "",
    level: int = logging.INFO,
) -> None:
    if summary:
        logger.log(level, "%s %s", prefix, summary)
    if isinstance(payload, dict):
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        text = str(payload)
    log_full_text(logger, prefix, text, level=level)
