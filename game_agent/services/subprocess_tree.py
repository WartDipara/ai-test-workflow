from __future__ import annotations

import locale
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


@dataclass(frozen=True, slots=True)
class PopenResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    shutdown: bool = False


def kill_process_tree(pid: int, *, force: bool = True) -> None:
    """终止进程及其子进程（Windows 用 taskkill /T）。"""
    if pid <= 0:
        return
    if sys.platform == "win32":
        flags = ["/T", "/PID", str(pid)]
        if force:
            flags.insert(0, "/F")
        try:
            subprocess.run(
                ["taskkill", *flags],
                capture_output=True,
                timeout=15.0,
                check=False,
            )
        except OSError as exc:
            logger.debug("taskkill failed pid=%s: %s", pid, exc)
        return
    try:
        import signal as sig

        os.killpg(os.getpgid(pid), sig.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _decode_stream_chunk(data: bytes) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode(locale.getpreferredencoding(), errors="replace")
        except (LookupError, UnicodeDecodeError):
            return data.decode("utf-8", errors="replace")


@dataclass
class _StreamCollector:
    chunks: list[bytes] = field(default_factory=list)
    _line_buf: str = ""

    def feed(self, data: bytes, *, prefix: str, stream_console: bool) -> None:
        if not data:
            return
        self.chunks.append(data)
        if not stream_console:
            return
        text = _decode_stream_chunk(data)
        self._line_buf += text
        while "\n" in self._line_buf:
            line, self._line_buf = self._line_buf.split("\n", 1)
            line = line.rstrip("\r")
            if line:
                logger.info("%s %s", prefix, line)

    def flush_tail(self, *, prefix: str, stream_console: bool) -> None:
        tail = self._line_buf.rstrip("\r")
        self._line_buf = ""
        if stream_console and tail:
            logger.info("%s %s", prefix, tail)

    def as_bytes(self) -> bytes:
        return b"".join(self.chunks)


def _pipe_reader(
    stream: object | None,
    collector: _StreamCollector,
    *,
    prefix: str,
    stream_console: bool,
    done: threading.Event,
) -> None:
    if stream is None:
        done.set()
        return
    try:
        while True:
            block = stream.read(4096)  # type: ignore[attr-defined]
            if not block:
                break
            collector.feed(block, prefix=prefix, stream_console=stream_console)
    except (OSError, ValueError):
        pass
    finally:
        collector.flush_tail(prefix=prefix, stream_console=stream_console)
        done.set()


def popen_communicate_poll(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout_s: float | None = None,
    poll_interval_s: float = 0.25,
    should_stop: Callable[[], bool] | None = None,
    stream_output: bool = False,
    stream_prefix: str = "[deploy]",
) -> PopenResult:
    """
    启动子进程并轮询等待；支持超时与外部停止回调。
    stream_output=True 时实时将 stdout/stderr 打到日志（控制台可见）。
    Windows 下为子进程创建独立进程组，便于 taskkill /T。
    """
    creationflags = CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    deadline = time.monotonic() + timeout_s if timeout_s is not None else None
    shutdown = False
    timed_out = False

    out_col = _StreamCollector()
    err_col = _StreamCollector()
    out_done = threading.Event()
    err_done = threading.Event()
    if stream_output:
        threading.Thread(
            target=_pipe_reader,
            args=(proc.stdout, out_col),
            kwargs={"prefix": stream_prefix, "stream_console": True, "done": out_done},
            daemon=True,
            name="subprocess-stdout",
        ).start()
        threading.Thread(
            target=_pipe_reader,
            args=(proc.stderr, err_col),
            kwargs={"prefix": f"{stream_prefix}:err", "stream_console": True, "done": err_done},
            daemon=True,
            name="subprocess-stderr",
        ).start()
    else:
        out_done.set()
        err_done.set()

    while proc.poll() is None:
        if should_stop and should_stop():
            shutdown = True
            kill_process_tree(proc.pid, force=True)
            break
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            kill_process_tree(proc.pid, force=True)
            break
        time.sleep(poll_interval_s)

    if stream_output:
        out_done.wait(timeout=15.0)
        err_done.wait(timeout=15.0)
        stdout = out_col.as_bytes()
        stderr = err_col.as_bytes()
    else:
        try:
            stdout, stderr = proc.communicate(timeout=10.0)
        except subprocess.TimeoutExpired:
            kill_process_tree(proc.pid, force=True)
            stdout, stderr = proc.communicate(timeout=5.0)
        stdout = stdout or b""
        stderr = stderr or b""

    returncode = proc.returncode if proc.returncode is not None else -1
    if shutdown:
        returncode = -2
    elif timed_out:
        returncode = -1

    return PopenResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        shutdown=shutdown,
    )
