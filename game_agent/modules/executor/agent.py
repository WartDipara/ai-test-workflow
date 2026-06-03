from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic_ai import Agent, RunContext

from game_agent.models.settings import AppConfig
from game_agent.modules.executor.deps import ExecutorAgentDeps
from game_agent.modules.executor.tooling import RunRequirement, ToolKind, make_tool_registrar
from game_agent.modules.executor.tooling.waits import (
    execute_check_in_game,
    execute_wait_for_game_running,
    execute_wait_for_package,
)
from game_agent.services.credentials import (
    credentials_status_message,
    load_game_credentials,
)
from game_agent.services.learned_skill_store import format_skill_list_for_tool, read_skill_file
from game_agent.services.login_flow_skill import read_login_flow_guide as load_login_flow_guide_text
from game_agent.services.llm_service import build_llm_model
from game_agent.utils.ocr_util import extract_text_with_bounds

# Re-export for controllers / agents package
__all__ = ["ExecutorAgentDeps", "build_executor_agent"]


def _prompt_path() -> Path:
    return Path(__file__).resolve().parent / "prompts" / "executor_system.en.txt"


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
    t = make_tool_registrar(agent)

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def list_learned_skills(ctx: RunContext[ExecutorAgentDeps], limit: int = 15) -> str:
        limit = max(1, min(int(limit), 30))
        return format_skill_list_for_tool(limit=limit)

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def read_learned_skill(ctx: RunContext[ExecutorAgentDeps], filename: str) -> str:
        return read_skill_file(filename)

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def read_login_flow_guide(ctx: RunContext[ExecutorAgentDeps]) -> str:
        """Generic mobile game login flow (skills/game-launch-ocr/SKILL.md)."""
        return load_login_flow_guide_text()

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def credentials_status(ctx: RunContext[ExecutorAgentDeps]) -> str:
        """Whether credentials.yaml is usable (no password plaintext)."""
        cfg = ctx.deps.app_config
        return credentials_status_message(
            cfg.credentials.file_path,
            settings_path=ctx.deps.settings_path,
        )

    @t(kind=ToolKind.INSTANT)
    async def fill_credential_field(
        ctx: RunContext[ExecutorAgentDeps],
        x: int,
        y: int,
        field: Literal["username", "password"],
    ) -> str:
        """Tap field center, clear, fill username or password from credentials.yaml."""
        cfg = ctx.deps.app_config
        try:
            cred = load_game_credentials(
                cfg.credentials.file_path,
                settings_path=ctx.deps.settings_path,
            )
        except (FileNotFoundError, ValueError) as e:
            return f"Cannot load credentials: {e}"

        value = cred.username if field == "username" else cred.password
        msg = ctx.deps.adb.fill_text_at(
            x,
            y,
            value,
            width=ctx.deps.screen_width,
            height=ctx.deps.screen_height,
        )
        if field == "password":
            msg = msg.replace(cred.password, "***")
        return (
            f"{msg}\n"
            f"Filled field {field}. "
            "Use get_ocr_summary or tap_and_observe before next step (e.g. tap Login)."
        )

    def _package_already_message(ctx: RunContext[ExecutorAgentDeps]) -> str:
        pkg = ctx.deps.app_config.game.package_name
        return (
            f"Package {pkg} already confirmed installed this run. "
            "Proceed with open_game_app — do not call wait_for_package_installed again."
        )

    @t(
        kind=ToolKind.WAIT,
        idempotent_attr="package_install_confirmed",
        idempotent_message=_package_already_message,
    )
    async def wait_for_package_installed(
        ctx: RunContext[ExecutorAgentDeps],
        timeout_s: float | None = None,
    ) -> str:
        """
        Call **once** after deploy. Polls ``pm path`` until the APK appears, then returns.
        Do not recheck install yourself.
        """
        return await execute_wait_for_package(ctx, timeout_s)

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def verify_adb_connection(ctx: RunContext[ExecutorAgentDeps]) -> str:
        return ctx.deps.adb.verify_connection()

    @t(kind=ToolKind.INSTANT, requirements=(RunRequirement.PACKAGE_INSTALLED,))
    async def open_game_app(ctx: RunContext[ExecutorAgentDeps]) -> str:
        """Launch game via am start -n. Call after wait_for_package_installed succeeds."""
        game = ctx.deps.app_config.game
        if not game.launch_activity.strip():
            return "Config error: game.launch_activity is empty"
        return ctx.deps.adb.launch_game(game.package_name, game.launch_activity)

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def force_stop_app(ctx: RunContext[ExecutorAgentDeps], package_name: str) -> str:
        return ctx.deps.adb.force_stop_package(package_name)

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def force_stop_apps(
        ctx: RunContext[ExecutorAgentDeps],
        package_names: list[str],
    ) -> str:
        return ctx.deps.adb.force_stop_packages(package_names)

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def capture_screenshot(ctx: RunContext[ExecutorAgentDeps], name: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:64]
        path = ctx.deps.artifact_root / f"{safe}.png"
        out = ctx.deps.adb.screencap_png(path)
        return str(out.resolve())

    @t(kind=ToolKind.INSTANT, check_stopped=False)
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
        return f"[Live OCR] screenshot={path.resolve()}\n{s}"

    @t(kind=ToolKind.INSTANT)
    async def tap_coordinate(ctx: RunContext[ExecutorAgentDeps], x: int, y: int) -> str:
        msg = ctx.deps.adb.tap(x, y, width=ctx.deps.screen_width, height=ctx.deps.screen_height)
        return f"{msg}\nHint: UI may have changed — call get_ocr_summary for fresh OCR."

    @t(kind=ToolKind.COMPOUND)
    async def tap_and_observe(
        ctx: RunContext[ExecutorAgentDeps],
        x: int,
        y: int,
        first_wait_s: float = 0.15,
        interval_s: float = 0.25,
        observations: int = default_tap_observe,
    ) -> str:
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

        return "\n".join(msg_lines)

    @t(kind=ToolKind.INSTANT)
    async def swipe_screen(
        ctx: RunContext[ExecutorAgentDeps],
        direction: str,
        duration_ms: int = 450,
    ) -> str:
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
            return f"Unknown direction: {direction!r}; use up/down/left/right"
        return ctx.deps.adb.swipe(cx, cy, cx + dx, cy + dy, duration_ms=duration_ms)

    @t(kind=ToolKind.INSTANT)
    async def press_back(ctx: RunContext[ExecutorAgentDeps]) -> str:
        return ctx.deps.adb.press_back()

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def wait_seconds(ctx: RunContext[ExecutorAgentDeps], seconds: float) -> str:
        seconds = max(0.5, min(float(seconds), 45.0))
        return ctx.deps.adb.wait_seconds(seconds)

    @t(kind=ToolKind.WAIT)
    async def wait_for_game_running(
        ctx: RunContext[ExecutorAgentDeps],
        summary: str,
        timeout_s: float | None = None,
    ) -> str:
        """Poll game.package_name process until present or timeout (milestone, not final success)."""
        return await execute_wait_for_game_running(ctx, summary, timeout_s)

    @t(kind=ToolKind.INSTANT)
    async def check_in_game(ctx: RunContext[ExecutorAgentDeps]) -> str:
        """
        Multimodal in-game check. Call after server_select, download, or likely HUD.
        Requires main_screen_confirm_rounds consecutive positives across calls.
        """
        return await execute_check_in_game(ctx)

    @t(kind=ToolKind.TERMINAL, check_stopped=False)
    async def report_flow_done(
        ctx: RunContext[ExecutorAgentDeps],
        success: bool,
        summary: str,
    ) -> str:
        if not success:
            ctx.deps.run_state.finished = True
            ctx.deps.run_state.success = False
            ctx.deps.run_state.note = summary[:2000]
            return "Recorded failure; stop calling tools."
        return (
            "Do not call this tool with success=true. "
            "Use check_in_game to confirm in-game completion."
        )

    return agent
