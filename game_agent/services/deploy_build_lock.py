from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from game_agent.paths import REPO_ROOT

logger = logging.getLogger(__name__)

_PROCESS_LOCK = threading.Lock()
_DEFAULT_LOCK_PATH = REPO_ROOT / "run_outputs" / ".deploy_build.lock"
_STALE_S = 3600.0


class DeployBuildLock:
    """deploy/build 阶段跨进程互斥，避免并发 cmake 竞争。"""

    def __init__(self, lock_path: Path | None = None, *, stale_s: float = _STALE_S) -> None:
        self._path = (lock_path or _DEFAULT_LOCK_PATH).resolve()
        self._stale_s = stale_s
        self._fd: int | None = None

    def acquire(self, *, timeout_s: float = 1800.0) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                self._fd = fd
                logger.debug("Acquired deploy build lock: %s", self._path)
                return
            except FileExistsError:
                if self._maybe_clear_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"deploy build lock timeout: {self._path}")
                time.sleep(0.5)

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to release deploy build lock: %s", exc)

    def _maybe_clear_stale(self) -> bool:
        try:
            age = time.time() - self._path.stat().st_mtime
        except OSError:
            return False
        if age < self._stale_s:
            return False
        try:
            self._path.unlink()
            logger.warning("Cleaned stale deploy build lock: %s (age=%.0fs)", self._path, age)
            return True
        except OSError:
            return False

    def __enter__(self) -> DeployBuildLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


@contextmanager
def deploy_build_locked(lock_path: Path | None = None):
    """进程内 + 文件级 deploy build 互斥。"""
    with _PROCESS_LOCK:
        lock = DeployBuildLock(lock_path)
        lock.acquire()
        try:
            yield lock
        finally:
            lock.release()
