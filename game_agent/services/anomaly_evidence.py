"""运行期 OCR+多模态网络异常证据，供 Modify / 失败报告引用。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ANOMALY_EVIDENCE_FILENAME = "anomaly_evidence.json"


def write_anomaly_evidence(
    artifact_root: Path,
    *,
    fatal_message: str,
    ocr_reason: str = "",
    ocr_summary: str = "",
    screenshot_path: str = "",
    vision_raw: str = "",
    vision_has_anomaly: bool = False,
    vision_stage: str = "",
    ui_stage: str = "",
) -> Path:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "fatal_message": fatal_message[:2000],
        "ui_stage": ui_stage,
        "ocr_reason": ocr_reason[:1000],
        "ocr_summary_excerpt": (ocr_summary or "")[:4000],
        "screenshot_path": screenshot_path,
        "vision_raw": (vision_raw or "")[:8000],
        "vision_has_anomaly": vision_has_anomaly,
        "vision_stage": vision_stage,
    }
    out = artifact_root / ANOMALY_EVIDENCE_FILENAME
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_anomaly_evidence(artifact_root: Path | None) -> dict[str, Any] | None:
    if artifact_root is None:
        return None
    path = artifact_root / ANOMALY_EVIDENCE_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def format_anomaly_evidence_for_ai(data: dict[str, Any] | None) -> str:
    if not data:
        return ""
    lines = [
        "[Runtime anomaly evidence — OCR + multimodal at fatal time]",
        f"fatal_message: {data.get('fatal_message', '')}",
        f"ui_stage: {data.get('ui_stage', '')}",
        f"ocr_reason: {data.get('ocr_reason', '')}",
        f"vision_has_anomaly: {data.get('vision_has_anomaly', False)}",
        f"vision_stage: {data.get('vision_stage', '')}",
    ]
    raw = str(data.get("vision_raw") or "").strip()
    if raw:
        lines.append(f"vision_json: {raw[:3000]}")
    ocr = str(data.get("ocr_summary_excerpt") or "").strip()
    if ocr:
        lines.append(f"ocr_excerpt:\n{ocr[:2000]}")
    shot = str(data.get("screenshot_path") or "").strip()
    if shot:
        lines.append(f"screenshot: {shot}")
    return "\n".join(lines)
