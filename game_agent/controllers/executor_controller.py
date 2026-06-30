from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from game_agent.config.loader import load_app_config
from game_agent.config.paths import resolve_repo_path
from game_agent.models.pipeline_phase import PipelinePhase
from game_agent.models.run_state import RunState
from game_agent.models.settings import AppConfig
from game_agent.models.task_config import TaskConfig
from game_agent.modules.run_context import AttemptContext
from game_agent.services.adb_service import AdbService
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.utils.ocr_util import configure_ocr, warmup_ocr
from game_agent.utils.ocr_worker import set_active_ocr_worker_key
from game_agent.utils.stage_logging import (
    bind_pipeline_stage,
    install_stage_aware_logging,
    reset_pipeline_stage,
)
from game_agent.views.console_view import ConsoleView

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    install_stage_aware_logging(level)


class ExecutorFlowController:
    """Controller：LangGraph 驱动游戏登录直至进游戏确认。"""

    def __init__(
        self,
        config_path: Path,
        *,
        app_config: TaskConfig | AppConfig | None = None,
    ) -> None:
        self._config_path = config_path
        self._app_config: TaskConfig | AppConfig | None = app_config

    def load_settings(self) -> TaskConfig | AppConfig:
        raw = load_app_config(self._config_path)
        art_dir = raw.agent.artifacts_dir
        art_dir = resolve_repo_path(art_dir)

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
        attempt_context: AttemptContext | None = None,
    ) -> RunState:
        if self._app_config is None:
            raise RuntimeError("Call load_settings() first")
        cfg = self._app_config
        view = ConsoleView(logger)

        if artifact_root is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            artifact_root = (cfg.agent.artifacts_dir / f"run_{stamp}").resolve()
        artifact_root = artifact_root.resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        executor_art = artifact_root / "executor"
        executor_art.mkdir(parents=True, exist_ok=True)
        view.banner(f"artifacts -> {artifact_root}")
        if audit is not None:
            audit.log_phase("executor", f"Executor phase start artifact={artifact_root.name}")

        adb = AdbService(cfg.adb.serial)
        w, h = adb.touch_size()
        view.banner(f"touch size {w}x{h}")

        configure_ocr(cfg.ocr, worker_key=cfg.adb.serial)
        set_active_ocr_worker_key(cfg.adb.serial)
        view.banner(
            f"OCR profile={cfg.ocr.model_profile} device={cfg.ocr.device_policy} "
            f"max_width={cfg.ocr.max_image_width}",
        )
        if cfg.ocr.warmup_on_start:
            view.banner("Warming up PaddleOCR…")
            warmup_ocr()

        run_state = RunState()
        if attempt_context is not None and attempt_context.deploy_package_verified:
            run_state.package_install_confirmed = True

        view.banner("Executor: LangGraph launch flow")
        if audit is not None:
            audit.log_phase("executor", "langgraph launch flow start")
        from game_agent.graphs.launch_flow import run_launch_graph_async

        graph_state = await run_launch_graph_async(
            app_config=cfg,
            adb=adb,
            run_state=run_state,
            artifact_root=artifact_root,
            settings_path=self._config_path.resolve(),
            audit=audit,
            attempt_context=attempt_context,
            screen_width=w,
            screen_height=h,
        )
        view.banner(
            f"LangGraph 结束 success={graph_state.success} "
            f"in_game={graph_state.in_game_confirmed} "
            f"note={graph_state.note[:120]!r}",
        )
        if audit is not None:
            audit.log_phase(
                "executor",
                f"langgraph end success={graph_state.success} "
                f"in_game={graph_state.in_game_confirmed}",
                note=graph_state.note[:500],
            )
        return graph_state


def run_executor_flow_sync(
    config_path: Path,
    *,
    app_config: TaskConfig | AppConfig | None = None,
    artifact_root: Path | None = None,
    audit: RunAuditLogger | None = None,
    attempt_context: AttemptContext | None = None,
) -> RunState:
    token = bind_pipeline_stage(PipelinePhase.EXECUTOR.value)
    try:
        ctrl = ExecutorFlowController(config_path, app_config=app_config)
        if app_config is None:
            ctrl.load_settings()
        else:
            configure_logging(app_config.logging.level)
        return asyncio.run(
            ctrl.run_async(
                artifact_root=artifact_root,
                audit=audit,
                attempt_context=attempt_context,
            ),
        )
    finally:
        reset_pipeline_stage(token)
