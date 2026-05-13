from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from pydantic_ai.messages import BinaryImage, ModelMessage

from game_agent.agents.login_agent import LoginAgentDeps, build_login_agent
from game_agent.config.loader import load_app_config
from game_agent.models.run_state import RunState
from game_agent.models.settings import AppConfig
from game_agent.services.adb_service import AdbService
from game_agent.services.credential_service import CredentialService
from game_agent.services.image_payload import build_screenshot_as_text_base64
from game_agent.services.vision_probe import probe_startup_for_llm
from game_agent.views.console_view import ConsoleView

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class LoginFlowController:
    """Controller：加载配置、组装依赖、驱动多轮「截图+模型+工具」循环。"""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._app_config: AppConfig | None = None

    def load_settings(self) -> AppConfig:
        raw = load_app_config(self._config_path)
        base = self._config_path.parent
        cred_path = raw.credentials.file_path
        if not cred_path.is_absolute():
            cred_path = (base / cred_path).resolve()
        art_dir = raw.agent.artifacts_dir
        if not art_dir.is_absolute():
            art_dir = (Path.cwd() / art_dir).resolve()
        self._app_config = raw.model_copy(
            update={
                "credentials": raw.credentials.model_copy(update={"file_path": cred_path}),
                "agent": raw.agent.model_copy(update={"artifacts_dir": art_dir}),
            },
        )
        assert self._app_config is not None
        configure_logging(self._app_config.logging.level)
        return self._app_config

    async def run_async(self) -> RunState:
        if self._app_config is None:
            raise RuntimeError("请先调用 load_settings()")
        cfg = self._app_config
        view = ConsoleView(logger)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        artifact_root = (cfg.agent.artifacts_dir / f"run_{stamp}").resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        view.banner(f"artifacts -> {artifact_root}")

        cred_svc = CredentialService(cfg.credentials.file_path.resolve())
        creds = cred_svc.load()

        if not cfg.llm.skip_vision_probe:
            vision_err = await probe_startup_for_llm(cfg.llm)
            if vision_err:
                logger.error("%s", vision_err)
                view.error(vision_err)
                run_state = RunState(
                    finished=True,
                    success=False,
                    note=vision_err[:4000],
                )
                view.banner("启动检查未通过，已中止（见上方 ERROR 与 note）")
                return run_state

        adb = AdbService(cfg.adb.serial)
        w, h = adb.wm_size()
        view.banner(f"wm size {w}x{h}")

        run_state = RunState()
        deps = LoginAgentDeps(
            app_config=cfg,
            adb=adb,
            credentials=creds,
            run_state=run_state,
            artifact_root=artifact_root,
            view=view,
            screen_width=w,
            screen_height=h,
        )

        agent = build_login_agent(cfg)
        history: list[ModelMessage] = []

        for r in range(cfg.agent.max_rounds):
            if run_state.finished:
                break
            view.round(r, "observe -> think -> act")

            shot_path = artifact_root / f"round_{r:03d}.png"
            try:
                adb.screencap_png(shot_path)
            except Exception as e:
                view.error(f"截屏失败: {e}")
                run_state.finished = True
                run_state.success = False
                run_state.note = str(e)
                break

            try:
                ui_summary = adb.summarize_clickable_elements()
            except Exception as e:
                ui_summary = f"[ui 摘要失败] {e}"

            log_tail = adb.logcat_tail(lines=120)

            preamble = (
                f"第 {r + 1}/{cfg.agent.max_rounds} 轮。"
                f"游戏包={cfg.game.package_name}。"
                "请根据截图、可点击控件摘要与 log 尾部决定下一步工具调用；"
                "若已达登录后主界面请调用 report_flow_done(success=true, ...)。"
            )
            ui_block = "=== 可点击控件摘要 ===\n" + ui_summary[:6000]
            log_block = "=== logcat 尾部（截断）===\n" + log_tail[:6000]

            if cfg.llm.image_transport == "text_base64":
                shot_ref = str(shot_path.resolve())
                img_block = await build_screenshot_as_text_base64(
                    shot_ref,
                    max_edge=cfg.agent.screenshot_max_edge,
                )
                user_parts: list[str | BinaryImage] = [
                    preamble,
                    img_block,
                    ui_block,
                    log_block,
                ]
            else:
                user_parts = [
                    preamble,
                    BinaryImage.from_path(shot_path),
                    ui_block,
                    log_block,
                ]

            try:
                result = await agent.run(user_parts, message_history=history, deps=deps)
            except Exception as e:
                view.error("agent.run 失败", exc_info=True)
                run_state.finished = True
                run_state.success = False
                run_state.note = str(e)
                break

            history.extend(result.new_messages())
            out = result.output or ""
            view.model_output(out)

            if run_state.finished:
                break

        if not run_state.finished:
            run_state.finished = True
            run_state.success = False
            run_state.note = f"已达最大轮次 {cfg.agent.max_rounds}，未收到 report_flow_done"

        view.banner(
            f"结束 success={run_state.success} note={run_state.note[:200]!r}",
        )
        return run_state


def run_login_flow_sync(config_path: Path) -> RunState:
    ctrl = LoginFlowController(config_path)
    ctrl.load_settings()
    return asyncio.run(ctrl.run_async())
