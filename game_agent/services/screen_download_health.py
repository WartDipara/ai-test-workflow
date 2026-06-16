from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from game_agent.services.vision_tools import is_network_anomaly_reason

_DOWNLOAD_STALL_STAGES = frozenset({
    "resource_download",
    "loading",
})

_NON_STALL_STAGES = frozenset({
    "login",
    "login_form",
    "sub_account_select",
    "privacy",
    "privacy_agree",
    "server_select",
    "server_check",
    "in_game",
    "clear",
})
_DOWNLOAD_PCT_RE = re.compile(r"(\d{1,3})%")
_OCR_LINE_RE = re.compile(
    r"^\s*(\d+)\s*,\s*(\d+)\s+(.+?)\s+[\d.]+\s*$",
)
_NETWORK_DIALOG_HINTS = (
    "网络连接失败",
    "网络异常",
    "资源下载失败",
    "下载失败",
    "连接超时",
    "连接失败",
)


@dataclass(frozen=True, slots=True)
class ScreenHealthVerdict:
    suspect: bool
    reason: str
    stage: str = ""
    progress: str = ""


def is_download_stall_watch_stage(stage: str) -> bool:
    """仅 resource_download/loading 参与下载停滞监视；login/unknown 等不参与。"""
    normalized = (stage or "unknown").strip()
    if normalized in _NON_STALL_STAGES:
        return False
    return normalized in _DOWNLOAD_STALL_STAGES


@dataclass
class ScreenProgressTracker:
    """跟踪资源下载进度是否停滞（忽略仅角标加速速率变化）。"""

    last_key: str = ""
    last_change_monotonic: float = field(default_factory=time.monotonic)
    last_stage: str = ""

    def observe(
        self,
        *,
        stage: str,
        progress: str,
        percent: int | None,
        stall_s: float,
    ) -> ScreenHealthVerdict:
        stage = (stage or "unknown").strip()
        progress = (progress or "").strip()
        key = f"{stage}|{progress}|{percent if percent is not None else ''}"
        now = time.monotonic()

        if key != self.last_key:
            self.last_key = key
            self.last_change_monotonic = now
            self.last_stage = stage

        elapsed = now - self.last_change_monotonic
        if not is_download_stall_watch_stage(stage):
            return ScreenHealthVerdict(False, "", stage, progress)

        if elapsed < stall_s:
            return ScreenHealthVerdict(False, "", stage, progress)

        detail = progress or (f"{percent}%" if percent is not None else stage)
        return ScreenHealthVerdict(
            True,
            f"stage={stage} progress unchanged for {elapsed:.0f}s ({detail})",
            stage,
            progress,
        )


def parse_percent_from_progress_text(progress: str) -> int | None:
    text = (progress or "").strip()
    if not text:
        return None
    match = _DOWNLOAD_PCT_RE.search(text)
    if not match:
        return None
    value = int(match.group(1))
    if 0 <= value <= 100:
        return value
    return None


def parse_download_percent_from_ocr(
    ocr_summary: str,
    *,
    min_y_ratio: float = 0.15,
    screen_h: int | None = None,
) -> int | None:
    """
    从 OCR 摘要提取资源下载百分比。
    默认忽略画面上方 15%（GameTurbo 加速角标/速率常在此区域）。
    """
    if not ocr_summary or ocr_summary.startswith("[OCR"):
        return None

    min_y = int((screen_h or 2400) * min_y_ratio)
    candidates: list[tuple[int, int]] = []

    for line in ocr_summary.splitlines():
        match = _OCR_LINE_RE.match(line.strip())
        if match:
            y = int(match.group(2))
            text = match.group(3)
            if y < min_y:
                continue
            pct_match = _DOWNLOAD_PCT_RE.search(text)
            if pct_match:
                value = int(pct_match.group(1))
                if 0 <= value <= 100:
                    candidates.append((y, value))
            continue
        if "," not in line:
            pct_match = _DOWNLOAD_PCT_RE.search(line)
            if pct_match:
                value = int(pct_match.group(1))
                if 0 <= value <= 100:
                    candidates.append((min_y + 1, value))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def detect_network_dialog_in_ocr(ocr_summary: str, *, min_y_ratio: float = 0.12) -> str:
    """检测画面中部/下部网络错误文案（非左上角加速角标）。"""
    if not ocr_summary:
        return ""
    min_y = int(2400 * min_y_ratio)
    for line in ocr_summary.splitlines():
        text = line
        match = _OCR_LINE_RE.match(line.strip())
        if match:
            y = int(match.group(2))
            if y < min_y:
                continue
            text = match.group(3)
        for hint in _NETWORK_DIALOG_HINTS:
            if hint in text:
                return hint
        if is_network_anomaly_reason(text):
            return text[:120]
    return ""
