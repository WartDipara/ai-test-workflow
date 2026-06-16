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


def primary_package_pid(pids: frozenset[str] | set[str] | list[str]) -> str | None:
    """多进程时取最小 pid 作为主进程；子进程 spawn/exit 不改变该值。"""
    numeric = [p for p in pids if str(p).strip().isdigit()]
    if not numeric:
        return None
    return min(numeric, key=int)


def package_primary_pid_changed(
    last_pids: frozenset[str],
    current_pids: frozenset[str],
) -> bool:
    """仅主进程 pid 变化时返回 True（忽略 WebView 等子进程增减）。"""
    if not last_pids or not current_pids:
        return False
    last_primary = primary_package_pid(last_pids)
    current_primary = primary_package_pid(current_pids)
    if last_primary is None or current_primary is None:
        return False
    return last_primary != current_primary


def mark_game_process_detected(
    run_state: RunState,
    *,
    game_package: str,
    reason: str,
) -> None:
    """Milestone only: process is up; executor continues until check_in_game confirms."""
    run_state.game_started = True
    if not run_state.note:
        run_state.note = reason[:2000]
    logger.info("Game process detected (%s): %s", game_package, reason)


# Backward-compatible alias
mark_game_started = mark_game_process_detected
