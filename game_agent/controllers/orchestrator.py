from __future__ import annotations

import asyncio
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from game_agent.config.loader import load_app_config
from game_agent.config.paths import resolve_repo_path
from game_agent.controllers.executor_controller import run_executor_flow_sync
from game_agent.controllers.parallel_phase_policy import (
    should_return_parallel_timeout_failure,
    should_signal_parallel_timeout_fatal,
)
from game_agent.controllers.log_monitor_controller import LogMonitor
from game_agent.controllers.network_anomaly_coordinator import NetworkAnomalyCoordinator
from game_agent.controllers.pre_controller import PreprocessingController
from game_agent.controllers.retry_controller import AnomalyHandler
from game_agent.controllers.session_controller import SessionCoordinator
from game_agent.exceptions import DeployPhaseError
from game_agent.models.pipeline_phase import PipelinePhase
from game_agent.models.run_state import RunState
from game_agent.models.run_failure import (
    ErrorCode,
    RunFailure,
    classify_failure,
    parse_error_code_from_text,
)
from game_agent.models.settings import AppConfig, ModulesSection
from game_agent.models.task_config import TaskConfig
from game_agent.models.task_context import TaskContext
from game_agent.models.task_runtime import TaskRuntimeRegistry
from game_agent.modules.observer_session.state import ObserverSessionState
from game_agent.modules.preprocessing.preprocessor import PreprocessResult
from game_agent.modules.retry.deploy_retry import run_deploy_with_ai_retry_sync
from game_agent.modules.run_context import AttemptContext
from game_agent.controllers.batch_urls import resolve_batch_urls
from game_agent.paths import GAMETURBO_MERGED_CONFIG_PATH
from game_agent.services.adb_service import AdbService
from game_agent.services.device_workspace_cleanup import (
    DevicePackageCleanupResult,
    prepare_device_for_new_task,
)
from game_agent.services.failure_report import (
    generate_and_save_attempt_failure_report,
    generate_failure_diagnosis_report,
)
from game_agent.services.game_launch import is_game_running
from game_agent.services.shutdown import (
    ShutdownRequested,
    get_shutdown_context,
    is_shutdown_requested,
)
from game_agent.services.gameturbo_config_retry import infer_blocked_stage
from game_agent.services.gameturbo_log import bootstrap_gameturbo_log, finalize_gameturbo_log
from game_agent.services.normal_exit import NormalExitState, confirm_in_game_normal_exit
from game_agent.services.pipeline_trace import (
    activate_pipeline_trace,
    deactivate_pipeline_trace,
    get_pipeline_tracer,
    trace_operation,
)
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.services.run_deliverable import (
    RunDeliverablePaths,
    create_task_output_dir,
    publish_failure_deliverable,
    publish_success_deliverable,
)
from game_agent.services.task_finalize import TaskRunJournal, finalize_task_deliverable
from game_agent.services.vision_probe import probe_startup_for_llm
from game_agent.utils.gameturbo_bootstrap import (
    discover_source_apk,
    find_merged_config_for_deliverable,
    merged_config_path,
    needs_gameturbo_deploy,
    needs_initial_preprocess,
    output_apk_path,
    parse_gid_from_apk_name,
    resolve_existing_game_config,
    resolve_game_config,
    resolve_task_gid,
    run_bootstrap_from_source,
)
from game_agent.utils.packages_cleanup import cleanup_deploy_artifacts, cleanup_task_packages

logger = logging.getLogger(__name__)

_EXECUTOR_DRAIN_AFTER_IN_GAME_S = 300.0


def _synthetic_in_game_run_state(attempt_ctx: AttemptContext) -> RunState:
    return RunState(
        in_game_confirmed=True,
        success=True,
        finished=True,
        game_started=True,
        note=attempt_ctx.get_in_game_note() or "In-game confirmed",
    )


async def _await_executor_after_in_game_signal(
    executor_task: asyncio.Task[RunState] | None,
    attempt_ctx: AttemptContext,
    *,
    timeout_s: float = _EXECUTOR_DRAIN_AFTER_IN_GAME_S,
) -> RunState:
    if executor_task is None:
        return _synthetic_in_game_run_state(attempt_ctx)
    if executor_task.done():
        try:
            state = executor_task.result()
        except Exception as exc:
            logger.warning(
                "Executor raised after in-game signal; using success signal: %s",
                exc,
            )
            return _synthetic_in_game_run_state(attempt_ctx)
        if state.in_game_confirmed:
            return state
        return _synthetic_in_game_run_state(attempt_ctx)
    try:
        state = await asyncio.wait_for(
            asyncio.shield(executor_task),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Executor cleanup exceeded %.0fs after in-game confirm; proceeding",
            timeout_s,
        )
        executor_task.cancel()
        return _synthetic_in_game_run_state(attempt_ctx)
    except Exception as exc:
        logger.warning(
            "Executor cleanup failed after in-game signal: %s",
            exc,
        )
        return _synthetic_in_game_run_state(attempt_ctx)
    if state.in_game_confirmed:
        return state
    return _synthetic_in_game_run_state(attempt_ctx)


class _FinishRun(Exception):
    """单轮尝试结束，携带 _finish_run 参数。"""

    def __init__(self, **finish_kwargs: object) -> None:
        self.finish_kwargs = finish_kwargs


class GameTestOrchestrator:
    """主编排器：按 modules 配置组装各子模块。"""

    def __init__(
        self,
        config_path: Path,
        *,
        task_context: TaskContext,
    ) -> None:
        self._config_path = config_path
        self._task_context = task_context
        self._settings: AppConfig | None = None
        self._app_config: TaskConfig | None = None
        self._adb: AdbService | None = None
        self._artifact_root: Path | None = None
        self._audit: RunAuditLogger | None = None
        self._last_executor_failure_reason = ""
        self._last_blocked_stage_hint = ""
        self._last_attempt_ui_stage = ""
        self._last_attempt_ui_progress = ""
        self._task_id = ""
        self._task_gid = ""
        self._deliverable: RunDeliverablePaths | None = None
        self._attempt_records: list[tuple[int, Path]] = []
        self._last_failure_reason = ""
        self._source_apk_path: Path | None = None
        self._observer_session_restarts = 0
        self._task_journal: TaskRunJournal | None = None
        self._preprocess_record: PreprocessResult | None = None
        self._preprocessing_enabled = False
        self._packages_startup_removed: list[str] = []
        self._device_startup_cleanup: list[DevicePackageCleanupResult] = []

    def _runtime(self):
        return self._task_context.runtime

    def _deploy_gid(self) -> str | None:
        gid = (self._task_gid or self._runtime().gid or "").strip()
        return gid or None

    def _rebind_config(self) -> TaskConfig:
        assert self._settings is not None
        bound = TaskConfig(self._settings, self._runtime())
        self._app_config = bound
        return bound

    def _load_config(self) -> None:
        raw = load_app_config(self._config_path)
        art_dir = resolve_repo_path(raw.agent.artifacts_dir)
        out_dir = resolve_repo_path(raw.gameturbo.run_outputs_dir)
        cache_dir = self._task_context.task_cache_dir.resolve()
        adb_serial = self._task_context.serial or raw.adb.serial
        self._settings = raw.model_copy(
            update={
                "agent": raw.agent.model_copy(update={"artifacts_dir": art_dir}),
                "gameturbo": raw.gameturbo.model_copy(update={"run_outputs_dir": out_dir}),
                "preprocessing": raw.preprocessing.model_copy(update={"apk_cache_dir": cache_dir}),
                "adb": raw.adb.model_copy(update={"serial": adb_serial}),
            },
        )
        self._rebind_config()
        assert self._app_config is not None
        self._adb = AdbService(self._app_config.adb.serial)

    def _prepare_device_at_task_start(self, cfg: AppConfig) -> list[DevicePackageCleanupResult]:
        """任务最早阶段：卸载设备上上一轮遗留的游戏安装。"""
        assert self._adb is not None
        with trace_operation(
            "device",
            "prepare_workspace_at_task_start",
            package=self._runtime().package_name,
        ) as rec:
            results = prepare_device_for_new_task(self._adb, self._runtime().package_name)
            uninstalled = [r.package for r in results if r.was_installed]
            rec.ok(
                checked=len(results),
                uninstalled=uninstalled,
            )
        return results

    def _prepare_packages_at_task_start(self) -> list[str]:
        """批跑：跳过 packages 全量清空，任务结束按 gid 精准清理。"""
        logger.debug("跳过 packages 全量清空（批跑 per-gid 清理）")
        return []

    def _sync_game_section_from_packages(self) -> None:
        """从 packages 内 APK 同步包名/启动 Activity 到 TaskRuntime 与内存 AppConfig。"""
        deploy_gid = self._deploy_gid()
        out_apk = output_apk_path(deploy_gid)
        apk: Path | None = out_apk if out_apk.is_file() else None
        if apk is None:
            try:
                apk = discover_source_apk(
                    gid=deploy_gid,
                    source_apk=self._runtime().source_apk,
                )
            except RuntimeError:
                return
        runtime = self._runtime()
        runtime.update_from_apk(apk)
        TaskRuntimeRegistry.register(runtime)
        self._rebind_config()

    def _check_shutdown(self) -> None:
        get_shutdown_context().raise_if_requested()

    def _log_module_flags(self, cfg: AppConfig) -> None:
        m = cfg.modules
        logger.info(
            "模块开关: executor=%s log_monitor=%s retry=%s max_retries=%s",
            m.executor,
            m.log_monitor,
            m.retry_on_failure,
            m.max_retries if m.retry_on_failure else 1,
        )

    def run(self) -> int:
        self._check_shutdown()
        cfg = self._app_config
        if cfg is None:
            self._load_config()
            cfg = self._app_config
        assert cfg is not None
        assert self._adb is not None

        self._packages_startup_removed = self._prepare_packages_at_task_start()

        mods = cfg.modules
        max_retries = mods.max_retries if mods.retry_on_failure else 1
        self._log_module_flags(cfg)

        self._task_id = self._task_context.task_id
        self._attempt_records = []
        self._last_failure_reason = ""
        self._deliverable = None
        self._task_journal = None
        self._source_apk_path = None

        # preprocessing before run_outputs: need packages/ to resolve gid
        self._preprocessing_enabled = cfg.preprocessing.enabled
        if cfg.preprocessing.enabled:
            with trace_operation("preprocessing", "run") as rec:
                preprocess_result = self._run_preprocessing(cfg)
                self._preprocess_record = preprocess_result
                if not preprocess_result.ok:
                    rec.fail(error=preprocess_result.message)
                    logger.error(
                        "预处理失败，终止任务: %s", preprocess_result.message
                    )
                    self._establish_task_deliverable(cfg, mods)
                    if self._task_journal is not None:
                        self._task_journal.log(
                            "preprocessing",
                            "failed",
                            message=preprocess_result.message,
                        )
                    pf = classify_failure(preprocess_result.message)
                    return self._finish_run(
                        success=False,
                        last_reason=pf.format(),
                        max_retries=1,
                        error_code=pf.code.value,
                    )
                rec.ok(
                    source_apk=str(preprocess_result.source_apk),
                    processed_apk=str(preprocess_result.processed_apk),
                    abis_kept=preprocess_result.abis_kept,
                    abis_removed=preprocess_result.abis_removed,
                )
        else:
            self._preprocess_record = None

        if (
            self._preprocess_record is not None
            and self._preprocess_record.ok
            and self._preprocess_record.processed_apk is not None
        ):
            self._apply_gameturbo_context_from_preprocess()

        self._sync_game_section_from_packages()
        self._runtime().require_identity()
        self._device_startup_cleanup = self._prepare_device_at_task_start(cfg)
        self._establish_task_deliverable(cfg, mods)

        for retry in range(1, max_retries + 1):
            self._check_shutdown()
            self._load_config()
            cfg = self._app_config
            assert cfg is not None
            assert self._adb is not None

            logger.info("=== 开始流程 第 %d/%d 次尝试 ===", retry, max_retries)
            if self._task_journal is not None:
                self._task_journal.log(
                    "attempt",
                    "start",
                    retry=retry,
                    max_retries=max_retries,
                )
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._artifact_root = (
                cfg.agent.artifacts_dir / f"retry_{retry}_{stamp}"
            ).resolve()
            self._artifact_root.mkdir(parents=True, exist_ok=True)

            self._audit = RunAuditLogger(
                self._artifact_root,
                enabled=cfg.logging.enable_run_audit,
            )
            if cfg.logging.enable_process_log_file:
                self._audit.attach_process_log_handler(cfg.logging.level)
            else:
                self._audit.detach_process_log_handler()
            self._audit.log_phase(
                "orchestrator",
                f"第 {retry}/{max_retries} 次尝试开始",
                modules=cfg.modules.model_dump(),
                task_id=self._task_id,
                deliverable_dir=str(self._deliverable.root),
            )
            self._attempt_records.append((retry, self._artifact_root))

            activate_pipeline_trace(
                artifact_root=self._artifact_root,
                enabled=cfg.logging.enable_pipeline_trace,
                verbose=cfg.logging.pipeline_trace_verbose,
            )
            try:
                self._run_one_attempt(cfg, retry, max_retries, mods)
            except _FinishRun as stop:
                return self._finish_run(**stop.finish_kwargs)
            finally:
                if self._audit is not None:
                    self._audit.detach_process_log_handler()
                deactivate_pipeline_trace()

        if self._audit is not None:
            self._audit.finalize(success=False, note="超过最大重试次数")
        logger.error("=== 最终异常结束，超过最大重试次数 ===")
        last = self._last_failure_reason or "超过最大重试次数"
        return self._finish_run(
            success=False,
            last_reason=last,
            max_retries=max_retries,
            error_code=parse_error_code_from_text(last) or ErrorCode.INTERNAL.value,
        )

    def _apply_gameturbo_context_from_preprocess(self) -> None:
        assert self._preprocess_record is not None
        processed = self._preprocess_record.processed_apk
        if processed is None:
            return
        source_apk = processed.resolve()
        gid = parse_gid_from_apk_name(source_apk)
        game_config_path, _created = resolve_game_config(gid)
        runtime = self._runtime()
        runtime.update_gameturbo(
            gid=gid,
            source_apk=source_apk,
            game_config_path=game_config_path,
        )
        TaskRuntimeRegistry.register(runtime)
        self._rebind_config()

    def _run_one_attempt(
        self,
        cfg: AppConfig,
        retry: int,
        max_retries: int,
        mods: ModulesSection,
    ) -> None:
        self._check_shutdown()
        try:
            with trace_operation("gameturbo", "prepare_context", retry=retry):
                self._prepare_gameturbo_context(cfg)
        except DeployPhaseError as e:
            self._on_attempt_failure(
                retry=retry,
                max_retries=max_retries,
                mods=mods,
                reason=f"GameTurbo deploy 失败: {e}",
                exc=e,
            )
            return
        except Exception as e:
            logger.error("GameTurbo 前置处理失败: %s", e)
            self._on_attempt_failure(
                retry=retry,
                max_retries=max_retries,
                mods=mods,
                reason=f"GameTurbo 前置处理失败: {e}",
                exc=e,
            )
            return

        self._load_config()
        cfg = self._app_config
        assert cfg is not None
        assert self._adb is not None
        self._sync_task_gid_from_config(cfg)
        self._check_shutdown()
        self._launch_game_after_prepare_context(cfg)
        self._check_shutdown()

        parallel_err = asyncio.run(
            self._run_parallel_game_phase(cfg, retry=retry, max_retries=max_retries),
        )
        if parallel_err:
            logger.warning("并行游戏阶段失败: %s", parallel_err)
            self._last_executor_failure_reason = parallel_err
            self._last_blocked_stage_hint = infer_blocked_stage(
                reason=parallel_err,
                ui_stage=self._last_attempt_ui_stage,
                ui_progress=self._last_attempt_ui_progress,
            )
            self._on_attempt_failure(
                retry=retry,
                max_retries=max_retries,
                mods=mods,
                reason=parallel_err,
            )
            # will_retry 时 _on_attempt_failure 仅 return；不可落入下方成功路径。
            return

        self._archive_gameturbo_log()
        if self._audit is not None:
            self._audit.finalize(success=True, note="parallel game phase passed")
        logger.info("=== 测试全部通过（check_in_game 已确认）===")
        raise _FinishRun(
            success=True,
            winning_retry=retry,
            max_retries=max_retries,
        )

    def _run_preprocessing(self, cfg: AppConfig):
        """执行预处理阶段：APK 下载/ABI 剥离。返回 PreprocessResult。"""
        from game_agent.utils.gameturbo_bootstrap import PACKAGES_DIR

        logger.info("阶段 0 [预处理]: APK 下载/ABI 剥离")
        apk_url = self._task_context.apk_url or None
        controller = PreprocessingController(
            cache_dir=cfg.preprocessing.apk_cache_dir,
            packages_dir=PACKAGES_DIR,
            preserved_abis=cfg.preprocessing.preserved_abis,
        )
        result = controller.run(apk_url=apk_url)
        if result.ok:
            logger.info("预处理完成: %s", result.message)
        else:
            logger.error("预处理失败: %s", result.message)
        return result

    def _snapshot_attempt_ui(self, attempt_ctx: AttemptContext) -> None:
        stage, progress = attempt_ctx.get_ui_observation()
        self._last_attempt_ui_stage = stage
        self._last_attempt_ui_progress = progress

    def _prior_attempt_brief_for_executor(self, retry: int) -> str:
        """第 2+ 次尝试时给执行者的简短事实摘要（不替代本轮完整 history）。"""
        if retry <= 1:
            return ""
        lines: list[str] = []
        if self._last_failure_reason:
            lines.append(f"Last failure: {self._last_failure_reason[:1200]}")
        if self._last_executor_failure_reason:
            lines.append(
                f"Last executor/monitor: {self._last_executor_failure_reason[:800]}",
            )
        if self._last_blocked_stage_hint:
            lines.append(f"Blocked stage hint: {self._last_blocked_stage_hint}")
        if self._deliverable is not None:
            from game_agent.services.gameturbo_config_retry import (
                format_last_patch_for_executor,
            )

            patch_lines = format_last_patch_for_executor(self._deliverable.root)
            if patch_lines:
                lines.append(patch_lines)
        if not lines:
            return f"Prior attempt {retry - 1} failed; no detailed reason cached."
        lines.append(
            "Redeploy reset app — re-check privacy/login. "
            "Priority: pass the stage that failed last time (often resource download) "
            "before calling check_in_game.",
        )
        return "\n".join(lines)

    async def _run_parallel_game_phase(
        self,
        cfg: AppConfig,
        *,
        retry: int,
        max_retries: int,
    ) -> str | None:
        """
        Executor (login → in-game) runs in parallel with Log/Screen monitors from game launch.
        Returns None on success, else failure reason.
        """
        assert self._adb is not None
        assert self._artifact_root is not None

        mods = cfg.modules
        monitors_on = mods.log_monitor
        if not mods.executor and not monitors_on:
            logger.info("[modules] executor and monitors off, skip game phase")
            return None

        if (
            mods.executor
            and cfg.llm_multimodal is not None
            and not cfg.observer.skip_vision_probe
        ):
            vision_err = await probe_startup_for_llm(cfg.llm, cfg.llm_multimodal)
            if vision_err:
                return f"Multimodal probe failed: {vision_err}"

        attempt_ctx = AttemptContext(
            attempt_index=retry,
            max_attempts=max_retries,
            prior_attempt_brief=self._prior_attempt_brief_for_executor(retry),
        )
        try:
            return await self._run_parallel_game_phase_body(
                cfg,
                retry=retry,
                max_attempts=max_retries,
                attempt_ctx=attempt_ctx,
            )
        finally:
            self._snapshot_attempt_ui(attempt_ctx)

    async def _run_parallel_game_phase_body(
        self,
        cfg: AppConfig,
        *,
        retry: int,
        max_attempts: int,
        attempt_ctx: AttemptContext,
    ) -> str | None:
        """Inner parallel phase (UI snapshot taken in outer finally)."""
        assert self._adb is not None
        assert self._artifact_root is not None
        mods = cfg.modules
        session_state = ObserverSessionState()
        exit_state = NormalExitState()
        stop_event = attempt_ctx.stop_all

        if self._audit is not None:
            self._audit.log_phase(
                PipelinePhase.OBSERVER.value,
                "parallel game phase start",
                executor=mods.executor,
                log_monitor=mods.log_monitor,
            )

        target_pkg = cfg.game.package_name.strip()
        if mods.executor and target_pkg:
            if not self._adb.is_package_installed(target_pkg):
                return (
                    f"[E1009] Package {target_pkg} not on device before executor. "
                    "Deploy may have failed at adb install — check deploy.log."
                )
            attempt_ctx.mark_deploy_package_verified()
            logger.info(
                "[Orchestrator] 设备已安装 %s，执行者可跳过 wait_for_package_installed",
                target_pkg,
            )

        if mods.log_monitor:
            from game_agent.services.gameturbo_log import clear_device_logcat

            clear_device_logcat(self._adb)
            bootstrap_gameturbo_log(self._adb, self._artifact_root)
            logger.info(
                "[Orchestrator] 已 logcat -c 并采集本轮 GameTurbo 快照（避免旧缓冲区误报）",
            )

        monitor_tasks: list[asyncio.Task[str | None]] = []
        log_mon: LogMonitor | None = None

        if mods.log_monitor:
            log_mon = LogMonitor(
                self._adb,
                cfg,
                self._artifact_root,
                session_state=session_state,
                audit=self._audit,
            )

            async def _log_task() -> str | None:
                result = await log_mon.run_until_anomaly(
                    stop_event,
                    skip_initial_bootstrap=True,
                )
                if result:
                    attempt_ctx.signal_fatal(result)
                return result

            monitor_tasks.append(asyncio.create_task(_log_task(), name="log_monitor"))

            if cfg.network_anomaly.enabled:
                net_watch = NetworkAnomalyCoordinator(
                    adb=self._adb,
                    app_config=cfg,
                    artifact_root=self._artifact_root,
                    attempt_context=attempt_ctx,
                    audit=self._audit,
                )

                async def _network_anomaly_task() -> str | None:
                    return await net_watch.run_until_confirmed(stop_event)

                monitor_tasks.append(
                    asyncio.create_task(_network_anomaly_task(), name="network_anomaly"),
                )

        session_coordinator = SessionCoordinator(
            adb=self._adb,
            app_config=cfg,
            artifact_root=self._artifact_root,
            session_state=session_state,
            audit=self._audit,
            log_monitor=log_mon,
            attempt_context=attempt_ctx,
        )
        session_task = asyncio.create_task(
            session_coordinator.watch(stop_event),
            name="session_coordinator",
        )

        async def _shutdown_watcher() -> str | None:
            ctx = get_shutdown_context()
            while not stop_event.is_set():
                if ctx.is_requested():
                    msg = f"Interrupted: {ctx.reason()}"
                    attempt_ctx.signal_fatal(msg)
                    return msg
                await asyncio.sleep(0.25)
            return None

        shutdown_task = asyncio.create_task(
            _shutdown_watcher(),
            name="shutdown_watcher",
        )

        executor_task: asyncio.Task | None = None
        if mods.executor:
            if self._audit is not None:
                self._audit.log_phase(
                    PipelinePhase.EXECUTOR.value,
                    "executor thread start (parallel with monitors)",
                )
            executor_task = asyncio.create_task(
                asyncio.to_thread(
                    run_executor_flow_sync,
                    self._config_path,
                    app_config=cfg,
                    artifact_root=self._artifact_root,
                    audit=self._audit,
                    attempt_context=attempt_ctx,
                ),
                name="executor",
            )
        elif mods.log_monitor:
            if not is_game_running(self._adb, cfg.game.package_name):
                logger.warning(
                    "executor=false but monitors on; game process not running (%s)",
                    cfg.game.package_name,
                )

        async def _cancel_pending(extra: asyncio.Task | None = None) -> None:
            stop_event.set()
            shutdown_task.cancel()
            session_task.cancel()
            for t in monitor_tasks:
                t.cancel()
            if executor_task is not None:
                executor_task.cancel()
            if extra is not None:
                extra.cancel()
            await asyncio.gather(
                shutdown_task,
                session_task,
                *monitor_tasks,
                *( [executor_task] if executor_task is not None else [] ),
                return_exceptions=True,
            )

        pending: set[asyncio.Task] = {session_task, shutdown_task, *monitor_tasks}
        if executor_task is not None:
            pending.add(executor_task)

        executor_state = None
        timed_out = False
        phase_ok = False
        deadline = time.monotonic() + cfg.game.timeout_s

        while pending and time.monotonic() < deadline and not phase_ok:
            if attempt_ctx.is_in_game_confirmed():
                phase_ok = True
                logger.info(
                    "Parallel phase: in-game confirmed via executor signal "
                    "(pending tasks=%d)",
                    len(pending),
                )
                break
            remaining = deadline - time.monotonic()
            done, pending = await asyncio.wait(
                pending,
                timeout=max(0.1, remaining),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                if attempt_ctx.is_in_game_confirmed():
                    phase_ok = True
                    logger.info(
                        "Parallel phase deadline reached but in-game already confirmed"
                    )
                else:
                    timed_out = True
                break

            for task in done:
                if task is shutdown_task:
                    try:
                        shutdown_err = task.result()
                    except asyncio.CancelledError:
                        continue
                    if shutdown_err:
                        await _cancel_pending()
                        return attempt_ctx.get_fatal_reason() or shutdown_err
                    continue

                if task is session_task:
                    try:
                        session_err = task.result()
                    except asyncio.CancelledError:
                        continue
                    if session_err:
                        attempt_ctx.signal_fatal(f"Session restart limit: {session_err}")
                        await _cancel_pending()
                        return attempt_ctx.get_fatal_reason()
                    continue

                if task in monitor_tasks:
                    try:
                        mon_err = task.result()
                    except asyncio.CancelledError:
                        continue
                    if mon_err:
                        await _cancel_pending()
                        return attempt_ctx.get_fatal_reason() or mon_err
                    continue

                if executor_task is not None and task is executor_task:
                    try:
                        executor_state = task.result()
                    except asyncio.CancelledError:
                        continue
                    if executor_state.in_game_confirmed:
                        phase_ok = True
                        if executor_task is not None:
                            executor_task.cancel()
                        session_task.cancel()
                        await asyncio.gather(
                            executor_task,
                            session_task,
                            return_exceptions=True,
                        )
                        pending.discard(executor_task)
                        pending.discard(session_task)
                        break
                    if executor_state.finished and not executor_state.success:
                        stop_event.set()
                        await _cancel_pending()
                        note = (executor_state.note or "").strip()
                        if note:
                            return note
                        code = executor_state.failure_code or "E1001"
                        return f"[{code}] Executor failed without detail"
                    stop_event.set()
                    await _cancel_pending()
                    return (
                        executor_state.note
                        or "Executor stopped without in-game confirmation"
                    )

        in_game_signaled = attempt_ctx.is_in_game_confirmed()
        if in_game_signaled:
            phase_ok = True

        if pending:
            if should_signal_parallel_timeout_fatal(
                timed_out=timed_out or time.monotonic() >= deadline,
                phase_ok=phase_ok,
                in_game_signaled=in_game_signaled,
                executor_in_game_confirmed=(
                    executor_state.in_game_confirmed if executor_state else None
                ),
            ):
                timed_out = True
                attempt_ctx.signal_fatal(
                    f"Parallel game phase timeout ({cfg.game.timeout_s:.0f}s) "
                    "without in-game confirmation",
                )
                stop_event.set()
                await _cancel_pending()
            elif phase_ok or in_game_signaled:
                logger.info(
                    "Parallel phase: draining executor after in-game success "
                    "(pending=%d)",
                    len(pending),
                )
                stop_event.set()
                shutdown_task.cancel()
                for t in monitor_tasks:
                    t.cancel()
                session_task.cancel()
                if executor_state is None and executor_task is not None:
                    executor_state = await _await_executor_after_in_game_signal(
                        executor_task,
                        attempt_ctx,
                    )
                await asyncio.gather(
                    session_task,
                    *monitor_tasks,
                    *(
                        [executor_task]
                        if executor_task is not None and not executor_task.done()
                        else []
                    ),
                    return_exceptions=True,
                )
                pending.clear()

        fatal = attempt_ctx.get_fatal_reason()
        if fatal and not in_game_signaled:
            return fatal

        exec_in_game = (
            executor_state.in_game_confirmed if executor_state is not None else None
        )
        if should_return_parallel_timeout_failure(
            timed_out=timed_out,
            phase_ok=phase_ok,
            in_game_signaled=in_game_signaled,
            executor_in_game_confirmed=exec_in_game,
            executor_enabled=mods.executor,
        ):
            return (
                f"Parallel game phase timeout ({cfg.game.timeout_s:.0f}s) "
                "without in-game confirmation"
            )

        if not mods.executor:
            if timed_out:
                logger.info("Monitors-only phase timed out after %.0fs (ok)", cfg.game.timeout_s)
            self._observer_session_restarts = session_state.restarts_count
            return None

        if executor_state is None:
            if in_game_signaled:
                executor_state = _synthetic_in_game_run_state(attempt_ctx)
            else:
                return "Executor module was enabled but did not complete"

        if not executor_state.in_game_confirmed:
            if in_game_signaled:
                executor_state = _synthetic_in_game_run_state(attempt_ctx)
            else:
                return (executor_state.note or "In-game not confirmed").strip()

        fatal_before_observe = attempt_ctx.get_fatal_reason()
        if fatal_before_observe:
            stop_event.set()
            for t in monitor_tasks:
                t.cancel()
            await asyncio.gather(*monitor_tasks, return_exceptions=True)
            return fatal_before_observe

        exit_result = await confirm_in_game_normal_exit(
            adb=self._adb,
            cfg=cfg,
            state=exit_state,
            session_state=session_state,
            audit=self._audit,
            summary=(executor_state.note or "In-game confirmed")[:2000],
        )

        stop_event.set()
        for t in monitor_tasks:
            t.cancel()
        if monitor_tasks:
            monitor_outcomes = await asyncio.gather(*monitor_tasks, return_exceptions=True)
            for outcome in monitor_outcomes:
                if isinstance(outcome, str) and outcome:
                    attempt_ctx.signal_fatal(outcome)
                elif outcome and not isinstance(outcome, BaseException):
                    attempt_ctx.signal_fatal(str(outcome))

        fatal_after_observe = attempt_ctx.get_fatal_reason()
        if fatal_after_observe:
            return fatal_after_observe

        if not exit_state.normal_exit_committed:
            return "In-game confirmed but normal exit was not committed"

        logger.info(
            "Parallel phase OK: %s | session_restarts=%d",
            exit_result.message[:300],
            session_state.restarts_count,
        )
        self._observer_session_restarts = session_state.restarts_count
        return None

    def _establish_task_deliverable(self, cfg: TaskConfig, mods: ModulesSection) -> None:
        """在预处理之后创建 run_outputs/{gid}_{task_id}，避免 unknown_* 占位目录。"""
        self._task_gid = resolve_task_gid(self._runtime().gid or "")
        self._source_apk_path = self._resolve_source_apk()
        self._deliverable = create_task_output_dir(
            cfg.gameturbo.run_outputs_dir,
            self._task_gid,
            self._task_id,
        )
        self._task_journal = TaskRunJournal(self._deliverable.root)
        logger.info(
            "任务产出目录: %s (gid=%s task_id=%s)",
            self._deliverable.root,
            self._task_gid,
            self._task_id,
        )
        self._task_journal.log(
            "task",
            "start",
            gid=self._task_gid,
            task_id=self._task_id,
            deliverable=str(self._deliverable.root),
            modules=mods.model_dump(),
        )
        if self._device_startup_cleanup:
            self._task_journal.log(
                "device",
                "prepared_at_start",
                results=[
                    {
                        "package": r.package,
                        "was_installed": r.was_installed,
                        "uninstall": (r.uninstall or "")[:200],
                        "skipped": r.skipped_reason,
                    }
                    for r in self._device_startup_cleanup
                ],
            )
        if self._packages_startup_removed:
            self._task_journal.log(
                "packages",
                "prepared_at_start",
                removed=self._packages_startup_removed,
            )
        if self._preprocessing_enabled:
            if self._preprocess_record is not None and self._preprocess_record.ok:
                pr = self._preprocess_record
                self._task_journal.log(
                    "preprocessing",
                    "ok",
                    message=pr.message,
                    source_apk=str(pr.source_apk or ""),
                    processed_apk=str(pr.processed_apk or ""),
                )
            else:
                self._task_journal.log("preprocessing", "skipped")
        else:
            self._task_journal.log("preprocessing", "skipped")

    def _sync_task_gid_from_config(self, cfg: TaskConfig) -> None:
        gid = (cfg.gameturbo.gid or "").strip()
        if not gid or gid == self._task_gid:
            return
        assert self._deliverable is not None
        old_root = self._deliverable.root
        self._task_gid = gid
        new_deliverable = create_task_output_dir(
            cfg.gameturbo.run_outputs_dir,
            gid,
            self._task_id,
        )
        new_root = new_deliverable.root
        if new_root == old_root:
            return
        new_root.mkdir(parents=True, exist_ok=True)
        if old_root.is_dir():
            for item in old_root.iterdir():
                dest = new_root / item.name
                if dest.exists():
                    continue
                shutil.move(str(item), str(dest))
            try:
                old_root.rmdir()
            except OSError:
                logger.warning(
                    "任务产出目录 gid 已更新: %s -> %s（旧目录未删除，请手动清理）",
                    old_root,
                    new_root,
                )
            else:
                logger.info("任务产出目录 gid 已更新: %s -> %s", old_root, new_root)
        self._deliverable = new_deliverable
        self._task_journal = TaskRunJournal(new_root)
        self._source_apk_path = self._resolve_source_apk()

    def _resolve_source_apk(self) -> Path | None:
        runtime = self._runtime()
        if runtime.source_apk is not None and runtime.source_apk.is_file():
            return runtime.source_apk.resolve()
        if (
            self._preprocess_record is not None
            and self._preprocess_record.processed_apk is not None
            and self._preprocess_record.processed_apk.is_file()
        ):
            return self._preprocess_record.processed_apk.resolve()
        try:
            return discover_source_apk(
                gid=self._deploy_gid(),
                source_apk=runtime.source_apk,
            )
        except RuntimeError:
            return None

    def _launch_game_after_prepare_context(self, cfg: TaskConfig) -> None:
        """deploy 不启动游戏；prepare_context 结束后由编排层拉起，再进入 executor。"""
        assert self._adb is not None
        if not cfg.modules.executor:
            return
        pkg = cfg.game.package_name.strip()
        if not pkg or not self._adb.is_package_installed(pkg):
            return
        try:
            msg = self._adb.launch_game(pkg, cfg.game.launch_activity)
            logger.info("prepare_context 后已启动游戏: %s", msg)
            if self._audit is not None:
                self._audit.log_phase(
                    PipelinePhase.INIT.value,
                    "deploy 后启动游戏",
                    package=pkg,
                    launch_result=msg[:500],
                )
        except Exception as e:
            logger.warning(
                "prepare_context 后启动游戏失败（executor 将重试 open_game_app）: %s",
                e,
            )

    def _cleanup_packages_after_attempt(self, *, will_retry: bool = False) -> None:
        if will_retry:
            logger.info(
                "将重试：保留 packages 下 deploy 产物，避免 Modify 后重复打包安装",
            )
            if self._audit is not None:
                self._audit.log_phase(
                    "packages",
                    "保留 deploy 产物以待下一轮",
                    gid=self._deploy_gid() or "",
                )
            return
        deploy_gid = self._deploy_gid()
        with trace_operation("packages", "cleanup_deploy_artifacts_after_attempt") as rec:
            removed = cleanup_deploy_artifacts(gid=deploy_gid)
            rec.ok(removed=removed)
        if removed and self._audit is not None:
            self._audit.log_phase(
                "packages",
                "本轮结束，已清理 deploy 产物",
                removed=removed,
            )

    def _detach_all_task_artifact_log_handlers(self) -> None:
        if self._audit is not None:
            self._audit.detach_process_log_handler()
        roots = [p for _, p in self._attempt_records]
        if roots:
            from game_agent.services.task_finalize import detach_process_log_handlers_for_roots

            n = detach_process_log_handlers_for_roots(roots)
            if n:
                logger.debug("已释放 %d 个 process.log FileHandler", n)

    def _finalize_packages_after_deliverable(self) -> None:
        assert self._app_config is not None
        self._source_apk_path = self._resolve_source_apk()
        deploy_gid = self._deploy_gid()
        summary: dict[str, list[str]] = {}
        with trace_operation("packages", "finalize_after_deliverable") as rec:
            summary = cleanup_task_packages(
                deploy_gid or "",
                self._source_apk_path,
            )
            rec.ok(**{k: len(v) for k, v in summary.items()})
        total = sum(len(v) for v in summary.values())
        if total:
            logger.info(
                "任务结束，packages 已清空: deploy=%s source=%s leftover=%s",
                summary.get("deploy"),
                summary.get("source"),
                summary.get("leftover"),
            )

    def _on_attempt_failure(
        self,
        *,
        retry: int,
        max_retries: int,
        mods: ModulesSection,
        reason: str,
        exc: BaseException | None = None,
    ) -> None:
        """Classify failure; retry only when error code is network/acceleration (E2xxx)."""
        failure = classify_failure(reason, exc=exc)
        self._last_failure_reason = failure.format()
        interrupted = isinstance(exc, ShutdownRequested) or is_shutdown_requested()
        will_retry = (
            not interrupted
            and failure.retryable
            and mods.retry_on_failure
            and retry < max_retries
        )

        if self._task_journal is not None:
            self._task_journal.log(
                "attempt",
                "failed",
                retry=retry,
                code=failure.code.value,
                retryable=failure.retryable,
                will_retry=will_retry,
                reason=failure.message[:800],
            )

        if failure.retryable:
            logger.warning(
                "可重试失败 %s（网络/加速）: %s",
                failure.code.value,
                failure.message[:300],
            )
        else:
            logger.error(
                "不可重试失败 %s（立即结束任务）: %s",
                failure.code.value,
                failure.format()[:500],
            )

        self._handle_failure_sync(
            retry,
            failure,
            run_retry_config=mods.retry_on_failure,
            max_retries=max_retries,
            will_retry=will_retry,
        )

        if will_retry:
            self._cleanup_packages_after_attempt(will_retry=True)
            return

        if interrupted:
            if self._audit is not None:
                self._audit.finalize(success=False, note="interrupted")
            reason_text = (
                exc.reason
                if isinstance(exc, ShutdownRequested)
                else get_shutdown_context().reason()
            )
            raise ShutdownRequested(reason_text)

        if self._audit is not None:
            self._audit.finalize(success=False, note=failure.format()[:500])
        finish = _FinishRun(
            success=False,
            last_reason=failure.format(),
            max_retries=max_retries,
            error_code=failure.code.value,
        )
        if exc is not None:
            raise finish from exc
        raise finish

    def _finish_run(
        self,
        *,
        success: bool,
        max_retries: int,
        winning_retry: int = 0,
        last_reason: str = "",
        error_code: str = "",
    ) -> int:
        assert self._deliverable is not None
        cfg = self._app_config
        assert cfg is not None

        # 尝试阶段 tracer 绑定 artifacts/retry_*；收尾前切换到 run_outputs，避免清理后写入失败。
        if get_pipeline_tracer() is not None:
            deactivate_pipeline_trace()
        extra_tracer = False
        if cfg.logging.enable_pipeline_trace:
            activate_pipeline_trace(
                artifact_root=self._deliverable.root,
                enabled=True,
                verbose=cfg.logging.pipeline_trace_verbose,
            )
            extra_tracer = True
        try:
            with trace_operation(
                "orchestrator",
                "finish_run",
                success=success,
                max_retries=max_retries,
            ) as rec:
                code = self._finish_run_inner(
                    cfg,
                    success=success,
                    max_retries=max_retries,
                    winning_retry=winning_retry,
                    last_reason=last_reason,
                    error_code=error_code,
                )
                rec.ok(exit_code=code)
                return code
        finally:
            if extra_tracer:
                deactivate_pipeline_trace()

    def _finish_run_inner(
        self,
        cfg: AppConfig,
        *,
        success: bool,
        max_retries: int,
        winning_retry: int,
        last_reason: str,
        error_code: str = "",
    ) -> int:
        if self._task_journal is not None:
            self._task_journal.log(
                "task",
                "finishing",
                success=success,
                winning_retry=winning_retry,
                last_reason=(last_reason or "")[:500],
                error_code=error_code or None,
            )

        if success:
            deploy_gid = self._deploy_gid()
            winning_root = dict(self._attempt_records).get(winning_retry)
            if winning_root is None and self._attempt_records:
                winning_root = self._attempt_records[-1][1]
            if winning_root is None:
                raise RuntimeError("测试通过但缺少 artifact 目录，无法记录产出元数据")
            config_path = find_merged_config_for_deliverable(
                deploy_gid or "",
                winning_artifact_root=winning_root,
            )
            if config_path is None:
                fallback = (
                    merged_config_path(deploy_gid)
                    if deploy_gid
                    else GAMETURBO_MERGED_CONFIG_PATH
                )
                raise RuntimeError(
                    f"测试通过但缺少 deploy 合并配置（已查 {winning_root} 与 {fallback}），"
                    "请确认 deploy.sh 已执行并生成合并配置"
                )
            passed = publish_success_deliverable(
                self._deliverable,
                game_config_path=config_path,
                winning_artifact_root=winning_root,
                winning_retry=winning_retry,
                total_attempts=len(self._attempt_records),
                session_restarts=self._observer_session_restarts,
            )
            self._finalize_packages_after_deliverable()
            logger.info("任务成功产出配置文件: %s", passed)
            exit_code = 0
        else:
            reason = last_reason or self._last_failure_reason or "未知失败"
            ai_report = asyncio.run(
                generate_failure_diagnosis_report(
                    cfg,
                    gid=self._task_gid,
                    task_id=self._task_id,
                    last_reason=reason,
                    attempt_records=self._attempt_records,
                    game_config_path=self._runtime().game_config_path,
                ),
            )
            publish_failure_deliverable(
                self._deliverable,
                attempt_artifact_roots=self._attempt_records,
                last_reason=reason,
                max_retries=max_retries,
                ai_report=ai_report,
                error_code=error_code,
            )
            self._finalize_packages_after_deliverable()
            logger.info(
                "任务失败产出已写入: %s（含 AI 报告 failure_report.md）",
                self._deliverable.root,
            )
            exit_code = 1

        self._detach_all_task_artifact_log_handlers()
        fin = finalize_task_deliverable(
            self._deliverable,
            success=success,
            max_retries=max_retries,
            winning_retry=winning_retry,
            last_reason=last_reason or self._last_failure_reason or "",
            attempt_records=self._attempt_records,
            preprocess_record=self._preprocess_record,
            preprocessing_enabled=self._preprocessing_enabled,
            artifacts_dir=cfg.agent.artifacts_dir,
            modules_summary=cfg.modules.model_dump(),
        )
        logger.info(
            "任务审查日志: %s | 已清理 artifacts 目录 %d 个",
            fin.final_log_path,
            len(fin.artifacts_removed),
        )
        if fin.artifacts_failed:
            logger.warning("部分 artifacts 清理失败: %s", fin.artifacts_failed)
        return exit_code

    def _run_gameturbo_deploy(
        self,
        cfg: TaskConfig,
        *,
        gid: str,
        game_config_path: Path,
        output_apk: Path,
    ) -> None:
        deploy_result = run_deploy_with_ai_retry_sync(
            cfg,
            gid=gid,
            game_config_path=game_config_path,
            artifact_root=self._artifact_root,
            audit=self._audit,
            phase=PipelinePhase.INIT.value,
        )
        if self._audit is not None:
            self._audit.log_phase(
                PipelinePhase.INIT.value,
                "GameTurbo deploy 已完成",
                gid=gid,
                deploy_log=str(deploy_result.log_path or ""),
                output_apk=str(output_apk),
            )

    def _prepare_gameturbo_context(self, cfg: TaskConfig) -> None:
        assert self._artifact_root is not None
        assert self._adb is not None
        deploy_gid = self._deploy_gid()
        output_apk = output_apk_path(deploy_gid)
        runtime = self._runtime()
        gid: str
        game_config_path: Path

        if needs_initial_preprocess(deploy_gid):
            if self._audit is not None:
                self._audit.log_phase(
                    PipelinePhase.INIT.value,
                    "进入 GameTurbo 前置处理",
                    output_apk=str(output_apk),
                )
            source_apk = self._resolve_source_apk()
            if source_apk is None:
                raise RuntimeError("缺少 source_apk，无法 bootstrap GameTurbo")
            result = run_bootstrap_from_source(source_apk, gid=deploy_gid)
            runtime.update_gameturbo(
                gid=result.gid,
                source_apk=result.source_apk,
                game_config_path=result.game_config_path,
            )
            TaskRuntimeRegistry.register(runtime)
            cfg = self._rebind_config()
            runtime.require_gameturbo()
            gid = result.gid
            game_config_path = result.game_config_path
            logger.info(
                "GameTurbo Init: gid=%s config=%s created=%s source=%s",
                result.gid,
                result.game_config_path,
                result.created_config,
                result.source_apk,
            )
            if self._audit is not None:
                self._audit.log_phase(
                    PipelinePhase.INIT.value,
                    "GameTurbo 配置已准备",
                    gid=result.gid,
                    game_config_path=str(result.game_config_path),
                    source_apk=str(result.source_apk),
                    created_config=result.created_config,
                )
        elif runtime.gid and runtime.game_config_path is not None:
            gid = runtime.gid
            game_config_path = runtime.game_config_path
            logger.info(
                "跳过 GameTurbo Init，使用已有上下文 gid=%s config=%s",
                gid,
                game_config_path,
            )
            if self._audit is not None:
                self._audit.log_phase(
                    PipelinePhase.INIT.value,
                    "跳过 bootstrap，复用 gameturbo 上下文",
                    gid=gid,
                    game_config_path=str(game_config_path),
                    output_apk=str(output_apk),
                )
        else:
            source_apk = discover_source_apk(
                gid=deploy_gid,
                source_apk=runtime.source_apk,
            )
            gid = parse_gid_from_apk_name(source_apk)
            game_config_path = resolve_existing_game_config(gid)
            runtime.update_gameturbo(
                gid=gid,
                source_apk=source_apk,
                game_config_path=game_config_path,
            )
            TaskRuntimeRegistry.register(runtime)
            cfg = self._rebind_config()
            logger.info(
                "已从现有 gameturbo 产物恢复上下文: gid=%s config=%s",
                gid,
                game_config_path,
            )
            if self._audit is not None:
                self._audit.log_phase(
                    PipelinePhase.INIT.value,
                    "恢复 GameTurbo 上下文",
                    gid=gid,
                    game_config_path=str(game_config_path),
                    source_apk=str(source_apk),
                    output_apk=str(output_apk),
                )

        cfg = self._rebind_config()
        target_pkg = cfg.game.package_name.strip()
        package_installed = bool(
            target_pkg and self._adb.is_package_installed(target_pkg),
        )
        if not needs_gameturbo_deploy(
            output_apk,
            package_installed=package_installed,
        ):
            logger.info(
                "跳过 deploy：设备已安装 %s（产物 %s）",
                target_pkg,
                output_apk.name if output_apk.is_file() else "已清理/缺失",
            )
            if self._audit is not None:
                self._audit.log_phase(
                    PipelinePhase.INIT.value,
                    "跳过 deploy，设备已安装",
                    gid=gid,
                    package=target_pkg,
                    output_apk=str(output_apk),
                )
            return

        if output_apk.is_file():
            logger.info(
                "本地已有 %s 但设备未安装 %s，重新 deploy",
                output_apk.name,
                target_pkg or "(unknown)",
            )
        else:
            logger.info("缺少 deploy 产物，开始 GameTurbo deploy gid=%s", gid)
        self._run_gameturbo_deploy(
            cfg,
            gid=gid,
            game_config_path=game_config_path,
            output_apk=output_apk,
        )

    def _archive_gameturbo_log(self) -> None:
        assert self._adb is not None
        if self._artifact_root is None:
            return
        finalize_gameturbo_log(self._adb, self._artifact_root)

    def _write_attempt_failure_report_sync(
        self,
        cfg: TaskConfig,
        retry_count: int,
        reason: str,
        *,
        will_retry: bool,
    ) -> None:
        if self._artifact_root is None:
            return
        gid = (self._task_gid or self._runtime().gid or "").strip() or "unknown"
        try:
            asyncio.run(
                generate_and_save_attempt_failure_report(
                    cfg,
                    retry_no=retry_count,
                    artifact_root=self._artifact_root,
                    reason=reason,
                    gid=gid,
                    will_retry=will_retry,
                    game_config_path=self._runtime().game_config_path,
                ),
            )
        except Exception as e:
            logger.warning("本轮 AI 失败报告生成失败: %s", e)

    def _handle_failure_sync(
        self,
        retry_count: int,
        failure: RunFailure,
        *,
        run_retry_config: bool,
        max_retries: int,
        will_retry: bool,
    ) -> None:
        assert self._adb is not None
        assert self._app_config is not None
        deliverable_root = (
            self._deliverable.root if self._deliverable is not None else None
        )
        handler = AnomalyHandler(
            adb=self._adb,
            app_config=self._app_config,
            config_path=self._config_path,
            artifact_root=self._artifact_root,
            task_deliverable_root=deliverable_root,
            blocked_stage_hint=self._last_blocked_stage_hint,
            audit=self._audit,
        )
        asyncio.run(
            handler.handle(
                retry_count,
                failure,
                run_retry_config=run_retry_config,
                will_retry=will_retry,
            ),
        )


def run_orchestrator(config_path: Path) -> int:
    raw = load_app_config(config_path)
    cache_dir = resolve_repo_path(raw.preprocessing.apk_cache_dir)
    urls = resolve_batch_urls(cache_dir)
    from game_agent.controllers.batch_runner import run_batch_orchestrator

    return run_batch_orchestrator(config_path, urls)
