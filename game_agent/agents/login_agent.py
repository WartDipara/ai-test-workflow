from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent, RunContext

from game_agent.models.run_state import RunState
from game_agent.models.settings import AppConfig
from game_agent.services.adb_service import AdbService
from game_agent.services.credential_service import Credentials
from game_agent.services.llm_service import build_llm_model
from game_agent.views.console_view import ConsoleView

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LoginAgentDeps:
    """注入 Agent 工具的运行期依赖（Controller 组装）。"""

    app_config: AppConfig
    adb: AdbService
    credentials: Credentials
    run_state: RunState
    artifact_root: Path
    view: ConsoleView
    screen_width: int
    screen_height: int


def _prompt_path() -> Path:
    return Path(__file__).resolve().parent.parent / "prompts" / "login_system.zh.txt"


def build_login_agent(app_config: AppConfig) -> Agent[LoginAgentDeps, str]:
    model = build_llm_model(app_config.llm)
    system_prompt = _prompt_path().read_text(encoding="utf-8")
    if app_config.llm.image_transport == "text_base64":
        system_prompt += (
            "\n\n补充：用户消息中可能包含【image_data】段落，即在纯文本里嵌入的 PNG 的 Base64；"
            "请将其视为屏幕截图进行理解（与常规多模态图片输入等价），再决定工具调用。"
        )
    agent: Agent[LoginAgentDeps, str] = Agent(
        model,
        deps_type=LoginAgentDeps,
        system_prompt=system_prompt,
        output_type=str,
    )

    @agent.tool
    async def verify_adb_connection(ctx: RunContext[LoginAgentDeps]) -> str:
        """确认当前 adb 目标设备处于 device 状态。"""
        msg = ctx.deps.adb.verify_connection()
        ctx.deps.view.tool("verify_adb_connection", msg)
        return msg

    @agent.tool
    async def launch_game(ctx: RunContext[LoginAgentDeps]) -> str:
        """启动配置中的游戏包（使用 activity 或 monkey）。"""
        g = ctx.deps.app_config.game
        msg = ctx.deps.adb.launch_game(g.package_name, g.activity)
        ctx.deps.view.tool("launch_game", msg[:800])
        return msg

    @agent.tool
    async def capture_screenshot(ctx: RunContext[LoginAgentDeps], name: str) -> str:
        """保存当前屏幕 PNG 到 artifacts 目录，返回本地路径（不含图像字节）。"""
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:64]
        path = ctx.deps.artifact_root / f"{safe}.png"
        out = ctx.deps.adb.screencap_png(path)
        ctx.deps.view.tool("capture_screenshot", str(out))
        return str(out.resolve())

    @agent.tool
    async def get_ui_summary(ctx: RunContext[LoginAgentDeps]) -> str:
        """获取当前界面可点击控件的文本摘要（来自 uiautomator dump）。"""
        s = ctx.deps.adb.summarize_clickable_elements()
        ctx.deps.view.tool("get_ui_summary", s[:1200])
        return s

    @agent.tool
    async def get_logcat_tail(ctx: RunContext[LoginAgentDeps], lines: int = 100) -> str:
        """拉取最近若干行 logcat（截断），辅助判断加载/报错。"""
        lines = max(20, min(lines, 300))
        text = ctx.deps.adb.logcat_tail(lines=lines)
        ctx.deps.view.tool("get_logcat_tail", f"{len(text)} chars")
        return text

    @agent.tool
    async def tap_coordinate(ctx: RunContext[LoginAgentDeps], x: int, y: int) -> str:
        """在屏幕像素坐标 (x,y) 执行点击。"""
        msg = ctx.deps.adb.tap(x, y, width=ctx.deps.screen_width, height=ctx.deps.screen_height)
        ctx.deps.view.tool("tap_coordinate", msg)
        return msg

    @agent.tool
    async def swipe_screen(
        ctx: RunContext[LoginAgentDeps],
        direction: str,
        duration_ms: int = 450,
    ) -> str:
        """从屏幕中心向 direction 滑动：up/down/left/right。"""
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
            return f"未知方向: {direction!r}，请使用 up/down/left/right"
        msg = ctx.deps.adb.swipe(cx, cy, cx + dx, cy + dy, duration_ms=duration_ms)
        ctx.deps.view.tool("swipe_screen", msg)
        return msg

    @agent.tool
    async def press_back(ctx: RunContext[LoginAgentDeps]) -> str:
        """按系统返回键。"""
        msg = ctx.deps.adb.press_back()
        ctx.deps.view.tool("press_back", msg)
        return msg

    @agent.tool
    async def fill_username_from_config(ctx: RunContext[LoginAgentDeps]) -> str:
        """从凭据文件读取用户名，并通过 adb input text 输入（需已聚焦到账号框）。"""
        u = ctx.deps.credentials.username
        ctx.deps.adb.input_text_adb(u)
        ctx.deps.view.tool("fill_username_from_config", "已写入用户名（内容已隐藏）")
        return "已写入用户名（长度=%d）" % len(u)

    @agent.tool
    async def fill_password_from_config(ctx: RunContext[LoginAgentDeps]) -> str:
        """从凭据文件读取密码，并通过 adb input text 输入（需已聚焦到密码框）。勿在对话中复述密码。"""
        p = ctx.deps.credentials.password
        ctx.deps.adb.input_text_adb(p)
        ctx.deps.view.tool("fill_password_from_config", "已写入密码（内容已隐藏）")
        return "已写入密码（长度=%d）" % len(p)

    @agent.tool
    async def wait_seconds(ctx: RunContext[LoginAgentDeps], seconds: float) -> str:
        """阻塞等待若干秒，用于下载/转圈等。"""
        seconds = max(0.5, min(float(seconds), 45.0))
        msg = ctx.deps.adb.wait_seconds(seconds)
        ctx.deps.view.tool("wait_seconds", msg)
        return msg

    @agent.tool
    async def report_flow_done(ctx: RunContext[LoginAgentDeps], success: bool, summary: str) -> str:
        """当你认为流程应结束时调用：success=true 表示已进入游戏主界面；否则说明阻塞原因。"""
        ctx.deps.run_state.finished = True
        ctx.deps.run_state.success = success
        ctx.deps.run_state.note = summary[:2000]
        ctx.deps.view.tool(
            "report_flow_done",
            f"success={success} summary={summary[:500]!r}",
        )
        return "已记录结束状态，请停止继续调用工具。"

    return agent
