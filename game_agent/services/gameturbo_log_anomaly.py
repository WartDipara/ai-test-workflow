from __future__ import annotations

import re
from datetime import datetime

# logcat 行首：06-03 10:32:15.362
_LOGCAT_LINE_TS = re.compile(
    r"^(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})",
)

# [BHOOK] OK: shutdown in ... = hook 安装成功，不是隧道关闭
_BHOOK_OK_LINE = re.compile(r"\[BHOOK\]\s+OK:", re.IGNORECASE)

# 高置信故障（对齐 skills/gameturbo_log_baseline_skill.md）
_FATAL_MARKERS: tuple[str, ...] = (
    "channel closed",
    "tunnel closed",
    "idle shutdown",
    "closing tunnel",
    "no streams for 300s",
)

# 曾误用裸 "shutdown"，会匹配 [BHOOK] OK: shutdown in libnetdutils.so


def logcat_line_age_seconds(line: str, *, reference: datetime | None = None) -> float | None:
    """
    解析 logcat 行首时间戳相对 reference 的秒数（仅 MM-DD，用 reference 的年份）。
    无法解析时返回 None。
    """
    ref = reference or datetime.now()
    m = _LOGCAT_LINE_TS.match(line.strip())
    if not m:
        return None
    month, day, hour, minute, second, ms = (int(g) for g in m.groups())
    try:
        ts = datetime(ref.year, month, day, hour, minute, second, ms * 1000)
    except ValueError:
        return None
    delta = (ref - ts).total_seconds()
    if delta < -300:
        try:
            ts = datetime(ref.year - 1, month, day, hour, minute, second, ms * 1000)
            delta = (ref - ts).total_seconds()
        except ValueError:
            return None
    return delta


def is_fatal_gameturbo_log_line(
    line: str,
    *,
    monitor_started_at: datetime,
    max_stale_seconds: float = 180.0,
) -> bool:
    """
    是否应对该 logcat 行 fail-fast。
    - 忽略 monitor 启动前过久的历史缓冲区（默认 >180s）
    - 忽略 [BHOOK] OK: ...（含 shutdown 系统调用 hook）
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("--------- "):
        return False

    age = logcat_line_age_seconds(stripped, reference=monitor_started_at)
    if age is not None and age > max_stale_seconds:
        return False

    if _BHOOK_OK_LINE.search(stripped):
        return False

    lower = stripped.lower()
    return any(marker in lower for marker in _FATAL_MARKERS)
