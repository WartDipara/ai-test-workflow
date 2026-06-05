from __future__ import annotations

import re

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


def is_fatal_gameturbo_log_line(line: str) -> bool:
    """
    检查 logcat 行是否包含高置信隧道故障标记。
    不做时间戳过滤——监控启动时 logcat 从当前时间开始读，
    闪退重启时会清空前一次积累的观察记录。
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("--------- "):
        return False

    if _BHOOK_OK_LINE.search(stripped):
        return False

    lower = stripped.lower()
    return any(marker in lower for marker in _FATAL_MARKERS)
