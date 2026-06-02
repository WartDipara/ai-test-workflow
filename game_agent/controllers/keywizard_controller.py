from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from pydantic_ai.messages import ModelMessage

from game_agent.config.loader import load_app_config
from game_agent.models.run_state import RunState
from game_agent.models.settings import AppConfig
from game_agent.modules.keywizard.agent import KeyWizardAgentDeps, build_keywizard_agent
from game_agent.paths import REPO_ROOT
from game_agent.services.adb_service import AdbService
from game_agent.services.llm_transcript import (
    format_new_llm_messages,
    format_user_parts_for_console,
)
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.services.session_memory import (
    HISTORY_FILE,
    MEMORY_FILE,
    load_conversation_history,
    load_session_memory,
    new_session_memory,
    save_conversation_history,
    save_session_memory,
)
from game_agent.services.success_skill_summarizer import write_skill_from_success_run
from game_agent.utils.ocr_util import configure_ocr, extract_text_with_bounds, warmup_ocr
from game_agent.views.console_view import ConsoleView

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class KeyWizardFlowController:
    """Controller：驱动多轮「截图+视觉职员+主脑工具」，启动按键精灵脚本。"""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._app_config: AppConfig | None = None

    def load_settings(self) -> AppConfig:
        raw = load_app_config(self._config_path)
        art_dir = raw.agent.artifacts_dir
        if not art_dir.is_absolute():
            art_dir = (Path.cwd() / art_dir).resolve()

        self._app_config = raw.model_copy(
            update={
                "agent": raw.agent.model_copy(update={"artifacts_dir": art_dir}),
            },
        )
        configure_logging(self._app_config.logging.level)
        return self._app_config

    async def run_async(
        self,
        *,
        artifact_root: Path | None = None,
        audit: RunAuditLogger | None = None,
    ) -> RunState:
        if self._app_config is None:
            raise RuntimeError("请先调用 load_settings()")
        cfg = self._app_config
        view = ConsoleView(logger)

        if artifact_root is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            artifact_root = (cfg.agent.artifacts_dir / f"run_{stamp}").resolve()
        artifact_root = artifact_root.resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        view.banner(f"artifacts -> {artifact_root}")
        if audit is not None:
            audit.log_phase("keywizard", f"开始执行者阶段 artifact={artifact_root.name}")

        adb = AdbService(cfg.adb.serial)
        w, h = adb.wm_size()
        view.banner(f"wm size {w}x{h}")

        configure_ocr(cfg.ocr)
        view.banner(
            f"OCR profile={cfg.ocr.model_profile} max_width={cfg.ocr.max_image_width}",
        )
        if cfg.ocr.warmup_on_start:
            view.banner("正在预热 PaddleOCR 模型…")
            warmup_ocr()

        target_pkg = cfg.keywizard.package_name
        game_pkg = cfg.game.package_name
        fg_pkg, fg_act = adb.current_foreground_app()
        if fg_pkg != target_pkg:
            view.banner("开局不在按键精灵，am start 启动（无需桌面 OCR 找图标）")
            adb.launch_game(target_pkg, cfg.keywizard.activity)
            adb.wait_seconds(cfg.keywizard.post_launch_wait_s)
            fg_pkg, fg_act = adb.current_foreground_app()
            view.banner(f"启动后前台={fg_pkg or 'unknown'}/{fg_act or 'unknown'}")

        run_state = RunState()
        session_id = artifact_root.name
        mem_path = artifact_root / MEMORY_FILE
        hist_path = artifact_root / HISTORY_FILE
        session_memory = load_session_memory(mem_path) or new_session_memory(session_id)
        history: list[ModelMessage] = load_conversation_history(hist_path) or []
        if history:
            logger.info("已恢复对话历史 messages=%d", len(history))
        logger.info("session_id=%s action_rounds=%d", session_id, len(session_memory.rounds))

        deps = KeyWizardAgentDeps(
            app_config=cfg,
            adb=adb,
            run_state=run_state,
            artifact_root=artifact_root,
            view=view,
            screen_width=w,
            screen_height=h,
            audit=audit,
            round_id=0,
        )

        agent = build_keywizard_agent(cfg)
        not_foreground_rounds = 0
        last_completed_round: int | None = None

        for r in range(cfg.agent.max_rounds):
            if run_state.game_started:
                view.banner(f"游戏进程 {game_pkg} 已启动，结束执行者轮次")
                break
            if run_state.finished:
                break
            view.round(r, "执行者: observe -> think -> act（仅按键精灵/启动游戏前）")
            deps.round_id = r
            if audit is not None:
                audit.log_round_start("keywizard", r, note=f"foreground 目标={target_pkg}")

            shot_path = artifact_root / f"round_{r:03d}.png"
            try:
                adb.screencap_png(shot_path)
            except Exception as e:
                view.error(f"截屏失败: {e}")
                run_state.finished = True
                run_state.success = False
                run_state.note = str(e)
                break

            fg_pkg, fg_act = adb.current_foreground_app()
            fg_line = f"{fg_pkg or 'unknown'}/{fg_act or 'unknown'}"

            if fg_pkg == target_pkg:
                view.banner("正在执行 OCR 文字识别…")
                try:
                    ocr_summary = extract_text_with_bounds(shot_path)
                except Exception as e:
                    ocr_summary = f"[OCR 识别失败或未安装 PaddleOCR] {e}"
                    logger.warning("OCR 失败: %s", e)
            else:
                view.banner("非按键精灵前台，跳过本轮开局 OCR")
                ocr_summary = (
                    "[跳过 OCR] 当前不在按键精灵内，无需在桌面/启动器上做 OCR。"
                    f"foreground={fg_line}。"
                    "请调用 open_keywizard_app（或等待控制器已执行的 am start）后再 get_ocr_summary。"
                )

            if fg_pkg != target_pkg:
                not_foreground_rounds += 1
            else:
                not_foreground_rounds = 0

            preamble = (
                f"第 {r + 1}/{cfg.agent.max_rounds} 轮。"
                f"屏幕尺寸={w}x{h}。"
                f"按键精灵包={target_pkg}。"
                f"目标脚本显示名={cfg.keywizard.script_display_name}。"
                f"广告初次观察建议等待={cfg.keywizard.ad_initial_wait_s:.1f}s。"
                f"当前前台应用={fg_line}。"
                f"连续非目标前台轮数={not_foreground_rounds}。"
                "本阶段仅使用 OCR + 主脑工具（多模态画面分析由 modules.screen_monitor 负责）。"
                f"测试游戏包名={game_pkg}。"
                f"等待游戏超时={cfg.game.launch_detect_timeout_s:.0f}s（轮询间隔 {cfg.game.launch_detect_poll_interval_s:.1f}s）。"
                "在按键精灵里点击脚本「启动/运行」后必须调用 wait_for_game_after_script_launch；"
                "该工具会阻塞计时并在限时内自动检测进程，成功或失败一次返回，勿用多次 wait_seconds 代替。"
                "请自主判断当前页面阶段、风险和下一步工具调用。"
            )
            fg_block = (
                "=== 前台应用检测(dumpsys) ===\n"
                f"foreground={fg_line}\n"
                f"target_package={target_pkg}\n"
                f"target_activity={cfg.keywizard.activity}\n"
            )
            memory_block = (
                "=== 已执行操作记录（系统自动）===\n"
                + session_memory.format_action_log()
            )
            ocr_block = (
                f"=== 屏幕 OCR（第 {r + 1} 轮开局快照，非实时）===\n"
                "说明：此块在调用主脑之前已生成。同轮内若已 tap/open_app/返回等，"
                "不得以本块为准，须用 get_ocr_summary 或 tap_and_observe 返回中的 OCR。\n"
                + ocr_summary[:8000]
            )
            user_parts: list[str] = [preamble, memory_block, fg_block, ocr_block]

            view.llm_user_bundle(r, format_user_parts_for_console(user_parts))

            try:
                result = await agent.run(user_parts, message_history=history, deps=deps)
            except Exception as e:
                view.error("agent.run 失败", exc_info=True)
                run_state.finished = True
                run_state.success = False
                err_text = str(e)
                if "status_code: 401" in err_text or "AuthenticationError" in err_text:
                    err_text = (
                        "主脑 LLM 请求认证失败（401）。请检查 config/settings.yaml 中 "
                        "llm.base_url、llm.api_key、llm.model_name 是否匹配且有效；"
                        f"原始错误: {err_text}"
                    )
                elif "status_code: 400" in err_text and "model names are" in err_text:
                    err_text = (
                        "主脑 LLM 返回 400：model_name 不被 API 接受。"
                        "请将 config/settings.yaml 中 llm.model_name 改为 "
                        "deepseek-v4-pro 或 deepseek-v4-flash（勿带 [1m] 等后缀）；"
                        f"原始错误: {err_text}"
                    )
                elif (
                    "status_code: 400" in err_text
                    and "UnsupportedParamsError" in err_text
                    and ("reasoning_effort" in err_text or "thinking" in err_text)
                ):
                    err_text = (
                        "主脑 LLM 返回 400：网关不支持 DeepSeek 思考模式参数"
                        "（reasoning_effort / thinking）。"
                        "请在 config/settings.yaml 的 llm 段设置 "
                        "deepseek_litellm_compat: true 后重试；"
                        f"原始错误: {err_text}"
                    )
                run_state.note = err_text[:4000]
                break

            new_msgs = result.new_messages()
            history.extend(new_msgs)
            out = result.output or ""
            if audit is not None:
                audit.log_transcript_bundle("keywizard", r, user_parts, new_msgs)
            session_memory.append_round(round_id=r, new_messages=new_msgs)
            save_session_memory(mem_path, session_memory)
            save_conversation_history(hist_path, history)
            view.llm_response_bundle(r, format_new_llm_messages(new_msgs))
            raw_json = result.new_messages_json()
            raw_json_text = (
                raw_json.decode("utf-8", errors="replace")
                if isinstance(raw_json, (bytes, bytearray))
                else str(raw_json)
            )
            view.llm_raw_messages_json(r, raw_json_text)
            has_reasoning = '"reasoning_content"' in raw_json_text
            view.banner(f"round={r} has_reasoning_content={has_reasoning}")
            raw_path = artifact_root / f"round_{r:03d}_new_messages.json"
            raw_path.write_text(raw_json_text, encoding="utf-8")
            view.model_output(out)
            last_completed_round = r

            if run_state.game_started:
                if audit is not None:
                    audit.log_phase("keywizard", f"游戏进程已启动，结束执行者 round={r}")
                break
            if run_state.finished:
                break

        if not run_state.game_started and not run_state.finished:
            run_state.finished = True
            run_state.success = False
            if not run_state.launch_wait_invoked:
                run_state.note = (
                    "执行者阶段结束：未调用 wait_for_game_after_script_launch，"
                    "尚未启动「等待游戏进程」定时检测"
                )
            elif not run_state.note:
                run_state.note = (
                    f"执行者阶段结束：{cfg.agent.max_rounds} 轮内未完成游戏启动流程 ({game_pkg})"
                )

        view.banner(
            f"结束 success={run_state.success} note={run_state.note[:200]!r}",
        )
        if audit is not None:
            audit.log_phase(
                "keywizard",
                f"执行者结束 success={run_state.success} game_started={run_state.game_started}",
                note=run_state.note[:500],
            )
        if run_state.success and last_completed_round is not None and cfg.agent.persist_learned_skill_on_success:
            skill_path = await write_skill_from_success_run(
                cfg,
                history,
                script_display_name=cfg.keywizard.script_display_name,
                final_summary=run_state.note or "",
                rounds_used=last_completed_round + 1,
                artifact_run_dir=artifact_root.name,
            )
            if skill_path:
                try:
                    rel = skill_path.relative_to(REPO_ROOT)
                except ValueError:
                    rel = skill_path
                view.banner(f"已生成已学技能: {rel}")
            else:
                view.banner("已学技能生成跳过（LLM 失败或过短）")

        return run_state


def run_keywizard_flow_sync(
    config_path: Path,
    *,
    artifact_root: Path | None = None,
    audit: RunAuditLogger | None = None,
) -> RunState:
    ctrl = KeyWizardFlowController(config_path)
    ctrl.load_settings()
    return asyncio.run(ctrl.run_async(artifact_root=artifact_root, audit=audit))
