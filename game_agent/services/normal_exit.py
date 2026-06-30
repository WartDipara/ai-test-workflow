from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from game_agent.models.settings import AppConfig
from game_agent.modules.observer_session.state import ObserverSessionState
from game_agent.services.adb_service import AdbService
from game_agent.services.run_audit_log import RunAuditLogger

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NormalExitState:
    """正常退出工具写入的运行期状态（单轮尝试）。"""

    in_game_main_confirmed: bool = False
    normal_exit_committed: bool = False
    note: str = ""


@dataclass(frozen=True, slots=True)
class NormalExitResult:
    ok: bool
    message: str
    observe_seconds: float


async def confirm_in_game_normal_exit(
    *,
    adb: AdbService,
    cfg: AppConfig,
    state: NormalExitState,
    session_state: ObserverSessionState | None = None,
    audit: RunAuditLogger | None = None,
    summary: str = "",
    observe_seconds: float | None = None,
) -> NormalExitResult:
    """
    AI 判定已进入游戏后调用：标记正常退出 → 等待观察窗口 → force-stop 游戏。
    不卸载游戏（失败收尾才卸载）。用于加速验证通过后的干净退出。
    """
    wait_s = (
        float(observe_seconds)
        if observe_seconds is not None
        else cfg.game.normal_exit_observe_s
    )
    wait_s = max(1.0, min(wait_s, 120.0))
    note = (summary or "In-game confirmed by AI, normal exit").strip()[:2000]

    state.in_game_main_confirmed = True
    state.normal_exit_committed = True
    state.note = note
    if session_state is not None:
        session_state.disable_monitoring()

    logger.info(
        "[NormalExit] 已进入游戏，正常退出流程开始 | 观察 %.1fs 后结束进程 | %s",
        wait_s,
        note[:200],
    )
    if audit is not None:
        audit.log_phase(
            "normal_exit",
            "confirm_in_game_normal_exit 已调用",
            observe_s=wait_s,
            summary=note[:500],
        )

    await asyncio.sleep(wait_s)

    game_pkg = (cfg.game.package_name or "").strip()
    packages = [game_pkg] if game_pkg else []

    logger.info("[NormalExit] Observe done, force-stop: %s", packages)
    msg = adb.force_stop_packages(packages)
    if audit is not None:
        audit.log_phase(
            "normal_exit",
            "已 force-stop 游戏",
            packages=packages,
            output=msg[:500],
        )

    out = (
        f"正常退出完成：已观察 {wait_s:.1f}s 并结束进程 {packages}。"
        f" {note}"
    )
    logger.info("[NormalExit] %s", out[:500])
    return NormalExitResult(ok=True, message=out, observe_seconds=wait_s)
