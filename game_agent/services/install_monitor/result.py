from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class InstallMonitorResult:
    """安装监控线程汇总（供 deploy 诊断）。"""

    brand: str = ""
    polls: int = 0
    clicks: int = 0
    errors: list[str] = field(default_factory=list)
    thread_crashed: bool = False

    def summary(self) -> str:
        parts = [
            f"brand={self.brand or 'unknown'}",
            f"polls={self.polls}",
            f"clicks={self.clicks}",
        ]
        if self.thread_crashed:
            parts.append("thread_crashed=true")
        if self.errors:
            parts.append(f"errors={'; '.join(self.errors[:3])}")
        return ", ".join(parts)
