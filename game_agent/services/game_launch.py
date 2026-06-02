from __future__ import annotations

import logging

from game_agent.models.run_state import RunState
from game_agent.services.adb_service import AdbService

logger = logging.getLogger(__name__)


def is_game_running(adb: AdbService, game_package: str) -> bool:
    return adb.is_package_running(game_package)


def get_package_pids(adb: AdbService, game_package: str) -> list[str]:
    """返回游戏包当前进程 pid 列表（空表示未运行）。"""
    pkg = (game_package or "").strip()
    if not pkg:
        return []
    try:
        r = adb._run(["shell", "pidof", pkg], timeout=10.0)
        if r.returncode == 0 and (r.stdout or "").strip():
            return [p for p in r.stdout.strip().split() if p]
    except Exception:
        pass
    try:
        r2 = adb._run(["shell", "pgrep", "-f", pkg], timeout=10.0)
        if r2.returncode == 0 and (r2.stdout or "").strip():
            return [p for p in r2.stdout.strip().splitlines() if p.strip()]
    except Exception:
        pass
    return []


def mark_game_started(
    run_state: RunState,
    *,
    game_package: str,
    reason: str,
) -> None:
    run_state.game_started = True
    run_state.finished = True
    run_state.success = True
    run_state.note = reason[:2000]
    logger.info("游戏进程已启动 (%s): %s", game_package, reason)
