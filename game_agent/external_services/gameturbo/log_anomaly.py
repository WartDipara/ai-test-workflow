from __future__ import annotations

import re

_BHOOK_OK_LINE = re.compile(r"\[BHOOK\]\s+OK:", re.IGNORECASE)

_FATAL_MARKERS: tuple[str, ...] = (
    "channel closed",
    "tunnel closed",
    "idle shutdown",
    "closing tunnel",
    "no streams for 300s",
)


def is_fatal_gameturbo_log_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("--------- "):
        return False
    if _BHOOK_OK_LINE.search(stripped):
        return False
    lower = stripped.lower()
    return any(marker in lower for marker in _FATAL_MARKERS)
