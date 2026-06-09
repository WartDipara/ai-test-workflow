from __future__ import annotations

import asyncio
import logging
import signal
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

logger = logging.getLogger(__name__)

_EXIT_SIGINT = 130


class ShutdownRequested(BaseException):
    """用户请求停止（SIGINT / 第二次 Ctrl+C 强制停止）。"""

    def __init__(self, reason: str = "shutdown requested", *, force: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.force = force


@dataclass
class ShutdownContext:
    """进程级停止令牌；线程与 asyncio 共享。"""

    _event: threading.Event = field(default_factory=threading.Event)
    _force: bool = False
    _reason: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def request_shutdown(self, reason: str = "SIGINT", *, force: bool = False) -> None:
        with self._lock:
            if self._event.is_set():
                if force:
                    self._force = True
                return
            self._reason = reason.strip() or "shutdown requested"
            self._force = force
            self._event.set()
        logger.warning(
            "[Shutdown] 已请求停止%s: %s",
            "（强制）" if force else "",
            self._reason,
        )

    def is_requested(self) -> bool:
        return self._event.is_set()

    def is_force(self) -> bool:
        with self._lock:
            return self._force

    def reason(self) -> str:
        with self._lock:
            return self._reason or "shutdown requested"

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_requested(self) -> None:
        if self.is_requested():
            raise ShutdownRequested(self.reason(), force=self.is_force())

    def bridge_to_asyncio(self, loop: asyncio.AbstractEventLoop) -> asyncio.Event:
        """将 threading.Event 桥接到 asyncio.Event（在目标 loop 内调用）。"""
        async_event = asyncio.Event()

        def _on_set() -> None:
            loop.call_soon_threadsafe(async_event.set)

        if self.is_requested():
            async_event.set()
        else:
            threading.Thread(
                target=lambda: (self._event.wait(), _on_set()),
                name="shutdown-bridge",
                daemon=True,
            ).start()
        return async_event


_GLOBAL: ShutdownContext | None = None
_GLOBAL_LOCK = threading.Lock()
_PREVIOUS_SIGINT = None
_PREVIOUS_SIGTERM = None


def get_shutdown_context() -> ShutdownContext:
    global _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is None:
            _GLOBAL = ShutdownContext()
        return _GLOBAL


def reset_shutdown_context() -> None:
    """测试用：重置全局上下文。"""
    global _GLOBAL
    with _GLOBAL_LOCK:
        _GLOBAL = ShutdownContext()


def _handle_signal(signum: int, _frame: object | None) -> None:
    ctx = get_shutdown_context()
    name = "SIGINT" if signum == signal.SIGINT else f"signal {signum}"
    if ctx.is_requested():
        logger.error("[Shutdown] 第二次中断，强制停止")
        ctx.request_shutdown(f"{name} (force)", force=True)
        raise SystemExit(_EXIT_SIGINT)
    ctx.request_shutdown(name)
    if signum == signal.SIGINT and _PREVIOUS_SIGINT is not None:
        signal.signal(signal.SIGINT, _PREVIOUS_SIGINT)


@contextmanager
def install_signal_handlers() -> Iterator[ShutdownContext]:
    """在 main 入口安装 SIGINT/SIGTERM 处理。"""
    global _PREVIOUS_SIGINT, _PREVIOUS_SIGTERM
    ctx = get_shutdown_context()
    if threading.current_thread() is threading.main_thread():
        try:
            _PREVIOUS_SIGINT = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, _handle_signal)
        except (ValueError, OSError):
            pass
        if hasattr(signal, "SIGTERM"):
            try:
                _PREVIOUS_SIGTERM = signal.getsignal(signal.SIGTERM)
                signal.signal(signal.SIGTERM, _handle_signal)
            except (ValueError, OSError):
                pass
    try:
        yield ctx
    finally:
        if threading.current_thread() is threading.main_thread():
            if _PREVIOUS_SIGINT is not None:
                try:
                    signal.signal(signal.SIGINT, _PREVIOUS_SIGINT)
                except (ValueError, OSError):
                    pass
            if _PREVIOUS_SIGTERM is not None and hasattr(signal, "SIGTERM"):
                try:
                    signal.signal(signal.SIGTERM, _PREVIOUS_SIGTERM)
                except (ValueError, OSError):
                    pass


def shutdown_exit_code(exc: BaseException | None = None) -> int:
    if isinstance(exc, ShutdownRequested) or isinstance(exc, KeyboardInterrupt):
        return _EXIT_SIGINT
    return 1


def is_shutdown_requested() -> bool:
    return get_shutdown_context().is_requested()
