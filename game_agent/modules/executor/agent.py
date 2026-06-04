from __future__ import annotations

import asyncio
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
from game_agent.services.accessibility_input import (
    dismiss_secure_keyboard_focus,
    fill_credential_via_accessibility,
    get_last_password_center_y,
    submit_login_after_password,
    verify_credential_via_accessibility,
)
from game_agent.services.credentials import (
    credentials_status_message,
    load_game_credentials,
)
from game_agent.services.dismiss_overlay import dismiss_overlay as run_dismiss_overlay
from game_agent.services.learned_skill_store import format_skill_list_for_tool, read_skill_file
from game_agent.services.llm_service import build_llm_model
from game_agent.services.login_form_ocr import resolve_login_form_targets
from game_agent.services.skill_catalog import read_login_flow_guide as load_login_flow_guide_text
from game_agent.services.skill_catalog import read_repo_skill as load_repo_skill_text
from game_agent.services.skill_catalog import read_skills_index as load_skills_index_text
from game_agent.services.vision_tools import run_analyze_screen
from game_agent.utils.ocr_util import (
    extract_text_with_bounds,
    format_device_ocr_for_executor,
    is_screencap_mostly_black,
)

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
    async def read_skills_index(ctx: RunContext[ExecutorAgentDeps]) -> str:
        """Skill 目录（skills/SKILL.md）：先读此文件，再按场景 read_repo_skill。"""
        return load_skills_index_text()

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def read_repo_skill(ctx: RunContext[ExecutorAgentDeps], skill_id: str) -> str:
        """阅读内置 skill 全文。skill_id 见 read_skills_index，如 game_launch_ocr。"""
        return load_repo_skill_text(skill_id)

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def read_login_flow_guide(ctx: RunContext[ExecutorAgentDeps]) -> str:
        """兼容别名：等同 read_repo_skill('game_launch_ocr')。新流程请先 read_skills_index。"""
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
        """
        凭据仅通过无障碍 setText（安全键盘下 OCR 无法读屏）。
        (x,y) 为填表前 get_ocr_summary 给出的字段参考坐标；无障碍会选真实 EditText。
        密码校验通过后：多策略提交登录（ENTER / 无障碍 Login / 填表前缓存坐标；
        不依赖收键盘后 OCR，避免安全键盘黑屏误判卡死）。
        """
        cfg = ctx.deps.app_config
        try:
            cred = load_game_credentials(
                cfg.credentials.file_path,
                settings_path=ctx.deps.settings_path,
            )
        except (FileNotFoundError, ValueError) as e:
            return f"Cannot load credentials: {e}"

        value = cred.username if field == "username" else cred.password
        ex = cfg.executor
        sw, sh = ctx.deps.screen_width, ctx.deps.screen_height

        msg = await asyncio.to_thread(
            fill_credential_via_accessibility,
            ctx.deps.adb.device_serial,
            x,
            y,
            value,
            width=sw,
            height=sh,
            field_label=field,
            settle_s=ex.credential_fill_settle_s,
            verify_after_fill=ex.credential_verify_after_fill,
            max_center_distance_px=ex.credential_fill_max_distance_px,
            retry_on_verify_fail=ex.credential_fill_retry_on_verify_fail,
        )

        if field == "password":
            msg = msg.replace(cred.password, "***")
            if (
                "Accessibility fill failed" not in msg
                and "VERIFY password: PASSED" in msg
            ):
                cached = ctx.deps.run_state.cached_login_button_xy
                cache_note = (
                    f"Cached Login={cached} '{ctx.deps.run_state.cached_login_button_text[:32]}'"
                    if cached
                    else "No cached Login — call get_ocr_summary on login screen before fill."
                )
                submit_msg = await asyncio.to_thread(
                    submit_login_after_password,
                    ctx.deps.adb.device_serial,
                    ctx.deps.adb,
                    width=sw,
                    height=sh,
                    cached_login_xy=cached,
                    password_y=get_last_password_center_y(ctx.deps.adb.device_serial),
                    artifact_root=ctx.deps.artifact_root,
                    screen_height=sh,
                    settle_s=ex.credential_fill_settle_s,
                    press_enter=ex.login_submit_press_enter,
                    use_cached_coords=ex.login_submit_use_cached_ocr_coords,
                    try_dismiss=ex.dismiss_keyboard_after_password,
                    press_back_on_dismiss=ex.dismiss_keyboard_press_back,
                    ocr_after_dismiss=ex.login_submit_ocr_after_dismiss,
                )
                msg = f"{msg}\n{cache_note}\n{submit_msg}"
        return msg

    @t(kind=ToolKind.INSTANT)
    async def verify_credential_field(
        ctx: RunContext[ExecutorAgentDeps],
        x: int,
        y: int,
        field: Literal["username", "password"],
    ) -> str:
        """不写入，仅校验 (x,y) 附近 EditText 是否已填入正确凭据（纠错/填完后复核）。"""
        cfg = ctx.deps.app_config
        try:
            cred = load_game_credentials(
                cfg.credentials.file_path,
                settings_path=ctx.deps.settings_path,
            )
        except (FileNotFoundError, ValueError) as e:
            return f"Cannot load credentials: {e}"
        value = cred.username if field == "username" else cred.password
        ex = cfg.executor
        msg = await asyncio.to_thread(
            verify_credential_via_accessibility,
            ctx.deps.adb.device_serial,
            x,
            y,
            value,
            width=ctx.deps.screen_width,
            height=ctx.deps.screen_height,
            field_label=field,
            max_center_distance_px=ex.credential_fill_max_distance_px,
        )
        if field == "password":
            msg = msg.replace(cred.password, "***")
        return msg

    @t(kind=ToolKind.INSTANT)
    async def dismiss_login_keyboard(ctx: RunContext[ExecutorAgentDeps]) -> str:
        """手动收起安全键盘（按设备 wm size 点击右上角空白区），便于 get_ocr_summary。"""
        ex = ctx.deps.app_config.executor
        sw, sh = await asyncio.to_thread(ctx.deps.adb.wm_size)
        return await asyncio.to_thread(
            dismiss_secure_keyboard_focus,
            ctx.deps.adb.device_serial,
            width=sw,
            height=sh,
            settle_s=ex.credential_fill_settle_s,
            press_back=ex.dismiss_keyboard_press_back,
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
        raw = await asyncio.to_thread(extract_text_with_bounds, path)
        sh = ctx.deps.screen_height
        s = format_device_ocr_for_executor(raw, screen_height=sh)
        black = is_screencap_mostly_black(path)
        cache_line = ""
        if not black:
            targets = resolve_login_form_targets(raw, screen_height=sh)
            if targets.login_button_xy is not None:
                ctx.deps.run_state.cached_login_button_xy = targets.login_button_xy
                ctx.deps.run_state.cached_login_button_text = targets.login_text
                lx, ly = targets.login_button_xy
                cache_line = (
                    f"\n[Cached Login for submit] ({lx},{ly}) "
                    f"'{targets.login_text[:40]}' — use before secure keyboard blocks OCR."
                )
        else:
            cache_line = (
                "\n[Screencap mostly BLACK] secure keyboard / password focus — "
                "OCR unreliable; fill via accessibility; submit uses ENTER/u2/cached Login."
            )
        return f"[Live OCR from device screencap] file={path.resolve()}\n{s}{cache_line}"

    @t(kind=ToolKind.INSTANT)
    async def tap_coordinate(ctx: RunContext[ExecutorAgentDeps], x: int, y: int) -> str:
        msg = ctx.deps.adb.tap(x, y, width=ctx.deps.screen_width, height=ctx.deps.screen_height)
        return f"{msg}\nHint: UI may have changed — call get_ocr_summary for fresh OCR."

    @t(kind=ToolKind.INSTANT)
    async def detect_checkbox(
        ctx: RunContext[ExecutorAgentDeps],
        prompt: str,
    ) -> str:
        """
        Use YOLO vision model to detect an untagged UI element and return its tap coordinates.
        Screencap is captured at device native resolution for accurate coordinate mapping.
        The returned (x, y) can be fed into tap_coordinate or tap_and_observe.
        """
        import httpx

        ts = datetime.now().strftime("%H%M%S_%f")
        path = ctx.deps.artifact_root / f"detect_{ts}.png"
        ctx.deps.adb.screencap_png(path)

        cfg = ctx.deps.app_config.detection
        with open(path, "rb") as f:
            try:
                resp = httpx.post(
                    cfg.api_url,
                    files={"file": f},
                    data={"prompt": prompt},
                    timeout=cfg.timeout_s,
                )
            except httpx.TimeoutException:
                return f"YOLO API timeout after {cfg.timeout_s}s: {cfg.api_url}"
            except httpx.RequestError as e:
                return f"YOLO API request failed: {e}"

        if resp.status_code != 200:
            return f"YOLO API error: status={resp.status_code}, body={resp.text[:500]}"

        data = resp.json()
        point = data.get("point")
        if not point or not isinstance(point, list) or len(point) != 2:
            return f"YOLO API invalid response format: {data}"

        x, y = point
        sw, sh = ctx.deps.screen_width, ctx.deps.screen_height
        if not (0 <= x < sw and 0 <= y < sh):
            return (
                f"YOLO returned out-of-bounds coordinates: ({x:.1f},{y:.1f}) "
                f"for screen {sw}x{sh}"
            )

        return (
            f"[Checkbox detection] prompt={prompt!r} "
            f"target=({x:.1f},{y:.1f}) "
            f"screenshot={path.resolve()}"
        )

    @t(kind=ToolKind.INSTANT, check_stopped=False)
    async def dismiss_overlay(
        ctx: RunContext[ExecutorAgentDeps],
    ) -> str:
        """
        Dismiss semi-transparent announcement / overlay / mask popup.
        Strategy: uiautomator2 finds known dismiss text → tap top-right corner → adb back.
        Call when OCR shows "开始游戏" mixed with announcement text, or after login/server_select.
        """
        return await asyncio.to_thread(
            run_dismiss_overlay,
            ctx.deps.adb.device_serial,
            ctx.deps.screen_width,
            ctx.deps.screen_height,
        )

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
            raw = extract_text_with_bounds(shot)
            ocr = format_device_ocr_for_executor(
                raw,
                screen_height=ctx.deps.screen_height,
            )[:3500]
            msg_lines.append(
                f"[observe#{i + 1}] device screencap={shot.resolve()}\n{ocr}",
            )
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
    async def analyze_screen(
        ctx: RunContext[ExecutorAgentDeps],
        reason: str = "",
    ) -> str:
        """
        On-demand multimodal screen analysis (stage / download / network dialog).
        Returns JSON: errorCode (0=call finished), completed, data{stage, has_anomaly, ...}.
        Call when OCR is ambiguous, after long wait, or before check_in_game.
        """
        return await run_analyze_screen(
            adb=ctx.deps.adb,
            cfg=ctx.deps.app_config,
            artifact_root=ctx.deps.artifact_root,
            round_id=ctx.deps.round_id,
            reason=reason,
            attempt_context=ctx.deps.attempt_context,
            audit=ctx.deps.audit,
        )

    @t(kind=ToolKind.INSTANT)
    async def check_in_game(ctx: RunContext[ExecutorAgentDeps]) -> str:
        """
        Multimodal in-game check (on-demand). Returns JSON with errorCode + data.confirmed.
        Requires main_screen_confirm_rounds consecutive positives when errorCode=0.
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
