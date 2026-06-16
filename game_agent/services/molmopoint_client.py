"""MolmoPoint FastAPI 客户端：上传截图调用 /predict 获取 checkbox 坐标。"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from game_agent.models.settings import MolmopointSection

logger = logging.getLogger(__name__)


def predict_points(
    image_path: Path,
    cfg: MolmopointSection,
) -> list[tuple[float, float]]:
    """
    调用 MolmoPoint /predict，返回 [(x, y), ...]。
    失败或配置未启用时返回空列表（不抛异常，便于回退 OCR 左推）。
    """
    if not cfg.is_active():
        return []

    url = f"{cfg.base_url.rstrip('/')}/predict"
    try:
        with httpx.Client(timeout=httpx.Timeout(cfg.timeout_s)) as client:
            with image_path.open("rb") as fh:
                response = client.post(
                    url,
                    files={"file": (image_path.name, fh, "image/png")},
                )
            response.raise_for_status()
            payload = response.json()
    except Exception as e:
        logger.warning("MolmoPoint predict failed (%s): %s", url, e)
        return []

    points: list[tuple[float, float]] = []
    for item in payload.get("points") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            points.append((float(item[0]), float(item[1])))
        except (TypeError, ValueError):
            continue
    return points
