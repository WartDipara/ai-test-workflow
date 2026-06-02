from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic_ai import Agent, RunContext

from game_agent.models.run_state import RunState
from game_agent.models.settings import AppConfig
from game_agent.services.adb_service import AdbService
from game_agent.services.game_launch import is_game_running, mark_game_started
from game_agent.services.learned_skill_store import format_skill_list_for_tool, read_skill_file
from game_agent.services.credentials import (
    credentials_status_message,
    load_game_credentials,
)
from game_agent.services.login_flow_skill import read_login_flow_guide
from game_agent.services.llm_service import build_llm_model
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.utils.ocr_util import extract_text_with_bounds
from game_agent.views.console_view import ConsoleView

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExecutorAgentDeps:
    """注入 Agent 工具的运行期依赖（Controller 组装）。"""

    app_config: AppConfig
    adb: AdbService
    run_state: RunState
    artifact_root: Path
    view: ConsoleView
    screen_width: int
    screen_height: int
    audit: RunAuditLogger | None = None
    round_id: int = 0
    settings_path: Path | None = None


def _log_tool(ctx: RunContext[ExecutorAgentDeps], name: str, args: Any, result: str) -> None:
    if ctx.deps.audit is not None:
        ctx.deps.audit.log_tool("executor", ctx.deps.round_id, name, args, result)


def _prompt_path() -> Path:
    return Path(__file__).resolve().parent / "prompts" / "executor_system.en.txt"


def _block_executor_if_game_running(ctx: RunContext[ExecutorAgentDeps]) -> str | None:
    if ctx.deps.run_state.game_started:
        return (
            "Game process already running; executor phase ended. "
            "Do not tap/swipe/back; stop calling tools — observer phase will take over."
        )
    return None


async def _wait_for_game_process(
    ctx: RunContext[ExecutorAgentDeps],
    *,
    summary: str,
    timeout_s: float | None,
) -> str:
    cfg = ctx.deps.app_config
    game_pkg = cfg.game.package_name
    run = ctx.deps.run_state
    run.launch_wait_invoked = True

    timeout = (
        float(timeout_s)
        if timeout_s is not None
        else cfg.game.launch_detect_timeout_s
    )
    timeout = max(15.0, min(timeout, 600.0))
    interval = cfg.game.launch_detect_poll_interval_s
    note = (summary or "Completed login/launch actions").strip()[:2000]
    run.note = note

    logger.info(
        "[Executor] 开始等待游戏进程 %s | 超时 %.0fs | 间隔 %.1fs | %s",
        game_pkg,
        timeout,
        interval,
        note[:120],
    )
    if ctx.deps.audit is not None:
        ctx.deps.audit.log_phase(
            "executor",
            "开始等待游戏进程",
            package=game_pkg,
            timeout_s=timeout,
            summary=note[:500],
        )

    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        if is_game_running(ctx.deps.adb, game_pkg):
            mark_game_started(
                run,
                game_package=game_pkg,
                reason=note or f"Game process detected after {attempt} poll(s)",
            )
            msg = (
                f"Success: game process {game_pkg} detected (poll #{attempt}). "
                "Executor phase ended — stop tap/swipe; switching to observer."
            )
            logger.info("[Executor] %s", msg)
            if ctx.deps.audit is not None:
                ctx.deps.audit.log_phase(
                    "executor", "游戏进程已启动", package=game_pkg, attempts=attempt
                )
            return msg

        remaining = deadline - time.monotonic()
        logger.info(
            "[Executor] 等待游戏进程 %s | 第 %d 次未检测到 | 剩余约 %.0fs",
            game_pkg,
            attempt,
            max(0.0, remaining),
        )
        await asyncio.sleep(min(interval, max(0.1, remaining)))

    run.finished = True
    run.success = False
    fail_msg = (
        f"Failed: game process {game_pkg} not detected within {timeout:.0f}s. "
        f"Context: {note}. "
        "Call report_flow_done(success=false) with blocker, or check package/login steps."
    )
    run.note = fail_msg[:2000]
    logger.warning("[Executor] %s", fail_msg)
    if ctx.deps.audit is not None:
        ctx.deps.audit.log_phase(
            "executor", "等待游戏进程超时", package=game_pkg, timeout_s=timeout
        )
    return fail_msg


def build_executor_agent(app_config: AppConfig) -> Agent[ExecutorAgentDeps, str]:
    model = build_llm_model(app_config.llm, deepseek=app_config.deepseek)
    system_prompt = _prompt_path().read_text(encoding="utf-8")
    default_tap_observe = app_config.agent.tap_observe_count
    agent: Agent[ExecutorAgentDeps, str] = Agent(
        model,
        deps_type=ExecutorAgentDeps,
        system_prompt=system_prompt,
        output_type=str,
    )

    @agent.tool
    async def list_learned_skills(ctx: RunContext[ExecutorAgentDeps], limit: int = 15) -> str:
        limit = max(1, min(int(limit), 30))
        s = format_skill_list_for_tool(limit=limit)
        ctx.deps.view.tool("list_learned_skills", s[:2000])
        _log_tool(ctx, "list_learned_skills", {"limit": limit}, s[:2000])
        return s

    @agent.tool
    async def read_learned_skill(ctx: RunContext[ExecutorAgentDeps], filename: str) -> str:
        s = read_skill_file(filename)
        ctx.deps.view.tool("read_learned_skill", f"{filename!r} {s[:1200]!r}")
        _log_tool(ctx, "read_learned_skill", {"filename": filename}, s[:2000])
        return s

    @agent.tool
    async def read_login_flow_guide(ctx: RunContext[ExecutorAgentDeps]) -> str:
        """读取仓库内通用游戏登录流程技能（隐私/公告/登录/选服等阶段模型，适用于各游戏）。"""
        s = read_login_flow_guide()
        ctx.deps.view.tool("read_login_flow_guide", s[:1200])
        _log_tool(ctx, "read_login_flow_guide", {}, s[:4000])
        return s

    @agent.tool
    async def credentials_status(ctx: RunContext[ExecutorAgentDeps]) -> str:
        """检查 credentials.yaml 是否可用（不返回密码明文）。"""
        cfg = ctx.deps.app_config
        s = credentials_status_message(
            cfg.credentials.file_path,
            settings_path=ctx.deps.settings_path,
        )
        ctx.deps.view.tool("credentials_status", s[:800])
        _log_tool(ctx, "credentials_status", {}, s[:2000])
        return s

    @agent.tool
    async def fill_credential_field(
        ctx: RunContext[ExecutorAgentDeps],
        x: int,
        y: int,
        field: Literal["username", "password"],
    ) -> str:
        """
        在 OCR 给出的输入框中心坐标处：点击 → 清空已有文字 → 填入 credentials.yaml 中的账号或密码。
        field 为 username 时填账号，password 时填密码。
        """
        blocked = _block_executor_if_game_running(ctx)
        if blocked:
            ctx.deps.view.tool("fill_credential_field", blocked)
            _log_tool(ctx, "fill_credential_field", {"x": x, "y": y, "field": field}, blocked)
            return blocked

        cfg = ctx.deps.app_config
        try:
            cred = load_game_credentials(
                cfg.credentials.file_path,
                settings_path=ctx.deps.settings_path,
            )
        except (FileNotFoundError, ValueError) as e:
            err = f"Cannot load credentials: {e}"
            ctx.deps.view.tool("fill_credential_field", err)
            _log_tool(ctx, "fill_credential_field", {"x": x, "y": y, "field": field}, err)
            return err

        value = cred.username if field == "username" else cred.password
        msg = ctx.deps.adb.fill_text_at(
            x,
            y,
            value,
            width=ctx.deps.screen_width,
            height=ctx.deps.screen_height,
        )
        safe_msg = msg
        if field == "password":
            safe_msg = msg.replace(cred.password, "***")
        ctx.deps.view.tool(
            "fill_credential_field",
            f"field={field} ({x},{y}) {safe_msg[:1000]}",
        )
        out = (
            f"{msg}\n"
            f"Filled field {field}. "
            "Use get_ocr_summary or tap_and_observe before next step (e.g. tap Login)."
        )
        _log_tool(
            ctx,
            "fill_credential_field",
            {"x": x, "y": y, "field": field},
            safe_msg[:2000],
        )
        return out

    @agent.tool
    async def verify_adb_connection(ctx: RunContext[ExecutorAgentDeps]) -> str:
        msg = ctx.deps.adb.verify_connection()
        ctx.deps.view.tool("verify_adb_connection", msg)
        _log_tool(ctx, "verify_adb_connection", {}, msg)
        return msg

    @agent.tool
    async def open_game_app(ctx: RunContext[ExecutorAgentDeps]) -> str:
        """打开测试游戏：使用配置 game.launch_activity 执行 am start -n。"""
        blocked = _block_executor_if_game_running(ctx)
        if blocked:
            ctx.deps.view.tool("open_game_app", blocked)
            _log_tool(ctx, "open_game_app", {}, blocked)
            return blocked
        game = ctx.deps.app_config.game
        if not game.launch_activity.strip():
            return "Config error: game.launch_activity is empty"
        msg = ctx.deps.adb.launch_game(game.package_name, game.launch_activity)
        ctx.deps.view.tool("open_game_app", msg[:800])
        _log_tool(ctx, "open_game_app", {}, msg[:2000])
        return msg

    @agent.tool
    async def force_stop_app(ctx: RunContext[ExecutorAgentDeps], package_name: str) -> str:
        msg = ctx.deps.adb.force_stop_package(package_name)
        ctx.deps.view.tool("force_stop_app", msg)
        _log_tool(ctx, "force_stop_app", {"package_name": package_name}, msg)
        return msg

    @agent.tool
    async def force_stop_apps(
        ctx: RunContext[ExecutorAgentDeps],
        package_names: list[str],
    ) -> str:
        msg = ctx.deps.adb.force_stop_packages(package_names)
        ctx.deps.view.tool("force_stop_apps", msg[:1200])
        _log_tool(ctx, "force_stop_apps", {"package_names": package_names}, msg[:2000])
        return msg

    @agent.tool
    async def capture_screenshot(ctx: RunContext[ExecutorAgentDeps], name: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:64]
        path = ctx.deps.artifact_root / f"{safe}.png"
        out = ctx.deps.adb.screencap_png(path)
        ctx.deps.view.tool("capture_screenshot", str(out))
        out_s = str(out.resolve())
        _log_tool(ctx, "capture_screenshot", {"name": name}, out_s)
        return out_s

    @agent.tool
    async def get_ocr_summary(
        ctx: RunContext[ExecutorAgentDeps],
        settle_seconds: float = 0.5,
    ) -> str:
        settle_seconds = max(0.0, min(float(settle_seconds), 3.0))
        if settle_seconds > 0:
            ctx.deps.adb.wait_seconds(settle_seconds)
        ts = datetime.now().strftime("%H%M%S_%f")
        path = ctx.deps.artifact_root / f"ocr_{ts}.png"
        ctx.deps.adb.screencap_png(path)
        s = extract_text_with_bounds(path)
        header = f"[Live OCR] screenshot={path.resolve()}\n"
        ctx.deps.view.tool("get_ocr_summary", (header + s)[:1200])
        full = header + s
        _log_tool(ctx, "get_ocr_summary", {"settle_seconds": settle_seconds}, full[:4000])
        return full

    @agent.tool
    async def tap_coordinate(ctx: RunContext[ExecutorAgentDeps], x: int, y: int) -> str:
        blocked = _block_executor_if_game_running(ctx)
        if blocked:
            ctx.deps.view.tool("tap_coordinate", blocked)
            _log_tool(ctx, "tap_coordinate", {"x": x, "y": y}, blocked)
            return blocked
        msg = ctx.deps.adb.tap(x, y, width=ctx.deps.screen_width, height=ctx.deps.screen_height)
        ctx.deps.view.tool("tap_coordinate", msg)
        out = f"{msg}\nHint: UI may have changed — call get_ocr_summary for fresh OCR."
        _log_tool(ctx, "tap_coordinate", {"x": x, "y": y}, out[:2000])
        return out

    @agent.tool
    async def tap_and_observe(
        ctx: RunContext[ExecutorAgentDeps],
        x: int,
        y: int,
        first_wait_s: float = 0.15,
        interval_s: float = 0.25,
        observations: int = default_tap_observe,
    ) -> str:
        blocked = _block_executor_if_game_running(ctx)
        if blocked:
            ctx.deps.view.tool("tap_and_observe", blocked)
            _log_tool(ctx, "tap_and_observe", {"x": x, "y": y}, blocked)
            return blocked
        first_wait_s = max(0.05, min(float(first_wait_s), 1.0))
        interval_s = max(0.05, min(float(interval_s), 1.5))
        observations = max(2, min(int(observations), 6))
        ts = datetime.now().strftime("%H%M%S_%f")

        tap_msg = ctx.deps.adb.tap(
            x,
            y,
            width=ctx.deps.screen_width,
            height=ctx.deps.screen_height,
        )
        if "Refused tap" in tap_msg or "rejected" in tap_msg.lower():
            ctx.deps.view.tool("tap_and_observe", tap_msg)
            _log_tool(ctx, "tap_and_observe", {"x": x, "y": y}, tap_msg)
            return tap_msg

        msg_lines: list[str] = [tap_msg]
        ctx.deps.adb.wait_seconds(first_wait_s)
        for i in range(observations):
            shot = ctx.deps.artifact_root / f"tap_obs_{ts}_{i + 1}.png"
            ctx.deps.adb.screencap_png(shot)
            ocr = extract_text_with_bounds(shot)[:2500]
            msg_lines.append(f"[observe#{i + 1}] screenshot={shot.resolve()}\n{ocr}")
            if i < observations - 1:
                ctx.deps.adb.wait_seconds(interval_s)

        msg = "\n".join(msg_lines)
        ctx.deps.view.tool("tap_and_observe", msg[:1600])
        _log_tool(ctx, "tap_and_observe", {"x": x, "y": y, "observations": observations}, msg[:4000])
        return msg

    @agent.tool
    async def swipe_screen(
        ctx: RunContext[ExecutorAgentDeps],
        direction: str,
        duration_ms: int = 450,
    ) -> str:
        blocked = _block_executor_if_game_running(ctx)
        if blocked:
            ctx.deps.view.tool("swipe_screen", blocked)
            _log_tool(ctx, "swipe_screen", {"direction": direction}, blocked)
            return blocked
        w, h = ctx.deps.screen_width, ctx.deps.screen_height
        cx, cy = w // 2, h // 2
        dist = int(min(w, h) * 0.25)
        dx, dy = 0, 0
        d = direction.lower().strip()
        if d == "up":
            dx, dy = 0, -dist
        elif d == "down":
            dx, dy = 0, dist
        elif d == "left":
            dx, dy = -dist, 0
        elif d == "right":
            dx, dy = dist, 0
        else:
            err = f"Unknown direction: {direction!r}; use up/down/left/right"
            _log_tool(ctx, "swipe_screen", {"direction": direction}, err)
            return err
        msg = ctx.deps.adb.swipe(cx, cy, cx + dx, cy + dy, duration_ms=duration_ms)
        ctx.deps.view.tool("swipe_screen", msg)
        _log_tool(ctx, "swipe_screen", {"direction": direction, "duration_ms": duration_ms}, msg)
        return msg

    @agent.tool
    async def press_back(ctx: RunContext[ExecutorAgentDeps]) -> str:
        blocked = _block_executor_if_game_running(ctx)
        if blocked:
            ctx.deps.view.tool("press_back", blocked)
            _log_tool(ctx, "press_back", {}, blocked)
            return blocked
        msg = ctx.deps.adb.press_back()
        ctx.deps.view.tool("press_back", msg)
        _log_tool(ctx, "press_back", {}, msg)
        return msg

    @agent.tool
    async def wait_seconds(ctx: RunContext[ExecutorAgentDeps], seconds: float) -> str:
        seconds = max(0.5, min(float(seconds), 45.0))
        msg = ctx.deps.adb.wait_seconds(seconds)
        ctx.deps.view.tool("wait_seconds", msg)
        _log_tool(ctx, "wait_seconds", {"seconds": seconds}, msg)
        return msg

    @agent.tool
    async def wait_for_game_running(
        ctx: RunContext[ExecutorAgentDeps],
        summary: str,
        timeout_s: float | None = None,
    ) -> str:
        """完成关键登录/启动操作后调用：轮询 game.package_name 直至进程出现或超时。"""
        blocked = _block_executor_if_game_running(ctx)
        if blocked:
            ctx.deps.view.tool("wait_for_game_running", blocked)
            _log_tool(ctx, "wait_for_game_running", {"summary": summary[:200]}, blocked)
            return blocked
        out = await _wait_for_game_process(ctx, summary=summary, timeout_s=timeout_s)
        ctx.deps.view.tool("wait_for_game_running", out[:1200])
        _log_tool(
            ctx,
            "wait_for_game_running",
            {"summary": summary[:500], "timeout_s": timeout_s},
            out[:4000],
        )
        return out

    @agent.tool
    async def report_flow_done(
        ctx: RunContext[ExecutorAgentDeps],
        success: bool,
        summary: str,
    ) -> str:
        if not success:
            ctx.deps.run_state.finished = True
            ctx.deps.run_state.success = False
            ctx.deps.run_state.note = summary[:2000]
            out = "Recorded failure; stop calling tools."
        else:
            out = (
                "Do not call this tool with success=true. "
                "Use wait_for_game_running for game launch completion."
            )
        ctx.deps.view.tool(
            "report_flow_done",
            f"success={success} summary={summary[:500]!r}",
        )
        _log_tool(ctx, "report_flow_done", {"success": success, "summary": summary[:500]}, out)
        return out

    return agent
