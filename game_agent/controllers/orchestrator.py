from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path

from game_agent.config.loader import load_app_config
from game_agent.controllers.game_entry_controller import GameEntryDetector
from game_agent.controllers.executor_controller import run_executor_flow_sync
from game_agent.exceptions import DeployPhaseError
from game_agent.controllers.log_monitor_controller import LogMonitor
from game_agent.controllers.pre_controller import PreprocessingController
from game_agent.controllers.retry_controller import AnomalyHandler
from game_agent.controllers.screen_monitor_controller import ScreenMonitor
from game_agent.controllers.session_controller import SessionCoordinator
from game_agent.models.pipeline_phase import PipelinePhase
from game_agent.models.settings import AppConfig, ModulesSection
from game_agent.modules.observer_session import ObserverSessionState
from game_agent.paths import GAMETURBO_MERGED_CONFIG_PATH
from game_agent.services.adb_service import AdbService
from game_agent.modules.retry.deploy_retry import run_deploy_with_ai_retry_sync
from game_agent.services.failure_report import (
    generate_and_save_attempt_failure_report,
    generate_failure_diagnosis_report,
)
from game_agent.services.game_launch import is_game_running
from game_agent.services.gameturbo_log import finalize_gameturbo_log
from game_agent.services.normal_exit import NormalExitState
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
    new_task_id,
    publish_failure_deliverable,
    publish_success_deliverable,
)
from game_agent.services.vision_probe import probe_startup_for_llm
from game_agent.utils.apk_util import update_settings_yaml_from_apk
from game_agent.utils.gameturbo_bootstrap import (
    discover_source_apk,
    needs_initial_preprocess,
    output_apk_path,
    parse_gid_from_apk_name,
    persist_gameturbo_context,
    resolve_existing_game_config,
    resolve_task_gid,
    run_bootstrap,
)
from game_agent.utils.packages_cleanup import cleanup_deploy_artifacts, remove_source_apk
from game_agent.utils.settings_yaml import upsert_top_level_section_fields

logger = logging.getLogger(__name__)


class _FinishRun(Exception):
    """单轮尝试结束，携带 _finish_run 参数。"""

    def __init__(self, **finish_kwargs: object) -> None:
        self.finish_kwargs = finish_kwargs


class GameTestOrchestrator:
    """主编排器：按 modules 配置组装各子模块。"""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._app_config: AppConfig | None = None
        self._adb: AdbService | None = None
        self._artifact_root: Path | None = None
        self._audit: RunAuditLogger | None = None
        self._last_executor_failure_reason = ""
        self._task_id = ""
        self._task_gid = ""
        self._deliverable: RunDeliverablePaths | None = None
        self._attempt_records: list[tuple[int, Path]] = []
        self._last_failure_reason = ""
        self._source_apk_path: Path | None = None
        self._observer_session_restarts = 0

    def _load_config(self) -> None:
        raw = load_app_config(self._config_path)
        art_dir = raw.agent.artifacts_dir
        if not art_dir.is_absolute():
            art_dir = (Path.cwd() / art_dir).resolve()
        out_dir = raw.gameturbo.run_outputs_dir
        if not out_dir.is_absolute():
            out_dir = (Path.cwd() / out_dir).resolve()
        cache_dir = raw.preprocessing.apk_cache_dir
        if not cache_dir.is_absolute():
            cache_dir = (Path.cwd() / cache_dir).resolve()
        self._app_config = raw.model_copy(
            update={
                "agent": raw.agent.model_copy(update={"artifacts_dir": art_dir}),
                "gameturbo": raw.gameturbo.model_copy(update={"run_outputs_dir": out_dir}),
                "preprocessing": raw.preprocessing.model_copy(update={"apk_cache_dir": cache_dir}),
            },
        )
        self._adb = AdbService(self._app_config.adb.serial)

    def _log_module_flags(self, cfg: AppConfig) -> None:
        m = cfg.modules
        logger.info(
            "模块开关: executor=%s game_entry_detect=%s log_monitor=%s "
            "screen_monitor=%s retry=%s max_retries=%s",
            m.executor,
            m.game_entry_detect,
            m.log_monitor,
            m.screen_monitor,
            m.retry_on_failure,
            m.max_retries if m.retry_on_failure else 1,
        )

    def run(self) -> int:
        try:
            cfg = self._app_config
            if cfg is None:
                self._load_config()
                cfg = self._app_config
            assert cfg is not None
            assert self._adb is not None

            mods = cfg.modules
            max_retries = mods.max_retries if mods.retry_on_failure else 1
            self._log_module_flags(cfg)

            self._task_id = new_task_id()
            self._task_gid = resolve_task_gid(cfg.gameturbo.gid)
            self._deliverable = create_task_output_dir(
                cfg.gameturbo.run_outputs_dir,
                self._task_gid,
                self._task_id,
            )
            self._attempt_records = []
            self._last_failure_reason = ""
            self._source_apk_path = self._resolve_source_apk(cfg)
            logger.info(
                "任务产出目录: %s (gid=%s task_id=%s)",
                self._deliverable.root,
                self._task_gid,
                self._task_id,
            )

            # ── 预处理阶段（retry 循环之前，仅执行一次）──
            if cfg.preprocessing.enabled:
                with trace_operation("preprocessing", "run") as rec:
                    preprocess_result = self._run_preprocessing(cfg)
                    if not preprocess_result.ok:
                        rec.fail(error=preprocess_result.message)
                        logger.error(
                            "预处理失败，终止任务: %s", preprocess_result.message
                        )
                        return 1
                    rec.ok(
                        source_apk=str(preprocess_result.source_apk),
                        processed_apk=str(preprocess_result.processed_apk),
                        abis_kept=preprocess_result.abis_kept,
                        abis_removed=preprocess_result.abis_removed,
                    )

            for retry in range(1, max_retries + 1):
                self._load_config()
                cfg = self._app_config
                assert cfg is not None
                assert self._adb is not None

                logger.info("=== 开始流程 第 %d/%d 次尝试 ===", retry, max_retries)
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
                    deactivate_pipeline_trace()

            if self._audit is not None:
                self._audit.finalize(success=False, note="超过最大重试次数")
            logger.error("=== 最终异常结束，超过最大重试次数 ===")
            return self._finish_run(
                success=False,
                last_reason=self._last_failure_reason or "超过最大重试次数",
                max_retries=max_retries,
            )
        finally:
            self._release_gameturbo_runtime_context()

    def _release_gameturbo_runtime_context(self) -> None:
        try:
            changed = upsert_top_level_section_fields(
                self._config_path,
                "gameturbo",
                {
                    "gid": "",
                    "game_config_path": "",
                    "source_apk": "",
                },
            )
        except Exception as e:
            logger.warning("任务结束后清理 gameturbo 运行态字段失败: %s", e)
            return
        if changed:
            logger.info(
                "任务结束，已清理 settings.yaml 的 gameturbo 运行态字段"
                "（gid/game_config_path/source_apk）",
            )

    def _run_one_attempt(
        self,
        cfg: AppConfig,
        retry: int,
        max_retries: int,
        mods: ModulesSection,
    ) -> None:
        try:
            with trace_operation("gameturbo", "prepare_context", retry=retry):
                self._prepare_gameturbo_context(cfg)
        except DeployPhaseError as e:
            init_reason = f"GameTurbo deploy 失败: {e}"
            logger.error("%s", init_reason)
            self._handle_failure_sync(
                retry,
                init_reason,
                run_retry_config=mods.retry_on_failure,
                max_retries=max_retries,
            )
            if mods.retry_on_failure:
                self._cleanup_packages_after_attempt()
                return
            if self._audit is not None:
                self._audit.finalize(success=False, note=init_reason[:500])
            raise _FinishRun(
                success=False,
                last_reason=init_reason,
                max_retries=max_retries,
            ) from e
        except Exception as e:
            logger.error("GameTurbo 前置处理失败: %s", e)
            init_reason = f"GameTurbo 前置处理失败: {e}"
            self._handle_failure_sync(
                retry,
                init_reason,
                run_retry_config=mods.retry_on_failure,
                max_retries=max_retries,
            )
            if mods.retry_on_failure:
                self._cleanup_packages_after_attempt()
                return
            if self._audit is not None:
                self._audit.finalize(success=False, note=init_reason[:500])
            raise _FinishRun(
                success=False,
                last_reason=init_reason,
                max_retries=max_retries,
            ) from e

        self._load_config()
        cfg = self._app_config
        assert cfg is not None
        assert self._adb is not None
        self._sync_task_gid_from_config(cfg)

        if not self._run_executor_phase(cfg):
            self._handle_failure_sync(
                retry,
                self._last_executor_failure_reason,
                run_retry_config=mods.retry_on_failure,
                max_retries=max_retries,
            )
            if mods.retry_on_failure:
                self._cleanup_packages_after_attempt()
                return
            if self._audit is not None:
                self._audit.finalize(
                    success=False,
                    note=self._last_executor_failure_reason[:500],
                )
            raise _FinishRun(
                success=False,
                last_reason=self._last_executor_failure_reason,
                max_retries=max_retries,
            )

        if mods.log_monitor or mods.screen_monitor or mods.game_entry_detect:
            anomaly = asyncio.run(self._run_observer_phase(cfg))
            if anomaly:
                logger.warning("观察者检测到异常: %s", anomaly)
                self._handle_failure_sync(
                    retry,
                    anomaly,
                    run_retry_config=mods.retry_on_failure,
                    max_retries=max_retries,
                )
                if mods.retry_on_failure:
                    self._cleanup_packages_after_attempt()
                    return
                if self._audit is not None:
                    self._audit.finalize(success=False, note=anomaly[:500])
                raise _FinishRun(
                    success=False,
                    last_reason=anomaly,
                    max_retries=max_retries,
                )
        else:
            logger.info("观察者模块均已关闭，跳过阶段2")

        self._archive_gameturbo_log()
        if self._audit is not None:
            self._audit.finalize(success=True, note="观察者阶段通过")
        logger.info("=== 测试全部通过 ===")
        raise _FinishRun(
            success=True,
            winning_retry=retry,
            max_retries=max_retries,
        )

    # ------------------------------------------------------------------
    # 预处理阶段（retry 循环之前执行一次）
    # ------------------------------------------------------------------

    def _run_preprocessing(self, cfg: AppConfig):
        """执行预处理阶段：APK 下载/ABI 剥离。返回 PreprocessResult。"""
        from game_agent.utils.gameturbo_bootstrap import PACKAGES_DIR

        logger.info("阶段 0 [预处理]: APK 下载/ABI 剥离")
        controller = PreprocessingController(
            cache_dir=cfg.preprocessing.apk_cache_dir,
            packages_dir=PACKAGES_DIR,
        )
        result = controller.run()
        if result.ok:
            logger.info("预处理完成: %s", result.message)
        else:
            logger.error("预处理失败: %s", result.message)
        return result

    def _run_executor_phase(self, cfg: AppConfig) -> bool:
        assert self._adb is not None
        if not cfg.modules.executor:
            logger.info("[模块 executor=false] 跳过阶段1")
            if cfg.modules.log_monitor or cfg.modules.screen_monitor:
                if not is_game_running(self._adb, cfg.game.package_name):
                    logger.warning(
                        "游戏进程未运行 (%s)，观察者模块可能无法正常工作",
                        cfg.game.package_name,
                    )
            return True

        logger.info("阶段 1 [执行者 / 模块 executor]")
        if self._audit is not None:
            self._audit.log_phase(
                PipelinePhase.EXECUTOR.value,
                "进入 OCR+AI 执行者阶段",
                gid=cfg.gameturbo.gid,
                game_config_path=str(cfg.gameturbo.game_config_path or ""),
            )
        ex_root = self._artifact_root / "executor" if self._artifact_root else None
        state = run_executor_flow_sync(
            self._config_path,
            artifact_root=ex_root,
            audit=self._audit,
        )
        if state.game_started:
            return True

        note = (state.note or "未知").strip()
        if state.launch_wait_invoked and not state.game_started:
            self._last_executor_failure_reason = (
                f"等待游戏进程失败 ({cfg.game.package_name}): {note}"
            )
        elif not state.launch_wait_invoked:
            self._last_executor_failure_reason = f"执行者阶段失败: {note}"
        else:
            self._last_executor_failure_reason = f"执行者阶段失败: {note}"
        logger.warning("%s", self._last_executor_failure_reason)
        return False

    async def _run_observer_phase(self, cfg: AppConfig) -> str | None:
        assert self._adb is not None
        assert self._artifact_root is not None

        needs_vision = (
            cfg.modules.screen_monitor or cfg.modules.game_entry_detect
        ) and not cfg.observer.skip_vision_probe
        if needs_vision:
            vision_err = await probe_startup_for_llm(cfg.llm, cfg.llm_multimodal)
            if vision_err:
                return f"Multimodal probe failed: {vision_err}"

        logger.info(
            "阶段 2 [观察者]: game_entry_detect=%s log_monitor=%s screen_monitor=%s",
            cfg.modules.game_entry_detect,
            cfg.modules.log_monitor,
            cfg.modules.screen_monitor,
        )
        if self._audit is not None:
            self._audit.log_phase(
                PipelinePhase.OBSERVER.value,
                "进入观察者阶段",
                gid=cfg.gameturbo.gid,
                game_config_path=str(cfg.gameturbo.game_config_path or ""),
            )

        stop_event = asyncio.Event()
        exit_state = NormalExitState()
        session_state = ObserverSessionState()
        monitor_tasks: list[asyncio.Task[str | None]] = []
        log_mon: LogMonitor | None = None

        if cfg.modules.log_monitor:
            log_mon = LogMonitor(
                self._adb,
                cfg,
                self._artifact_root,
                session_state=session_state,
                audit=self._audit,
            )
            monitor_tasks.append(
                asyncio.create_task(log_mon.run_until_anomaly(stop_event)),
            )

        if cfg.modules.screen_monitor:
            screen_mon = ScreenMonitor(
                self._adb,
                cfg,
                self._artifact_root,
                session_state=session_state,
                audit=self._audit,
            )
            monitor_tasks.append(
                asyncio.create_task(screen_mon.run_until_anomaly(stop_event)),
            )

        session_coordinator = SessionCoordinator(
            adb=self._adb,
            app_config=cfg,
            artifact_root=self._artifact_root,
            session_state=session_state,
            audit=self._audit,
            log_monitor=log_mon,
        )
        session_task = asyncio.create_task(session_coordinator.watch(stop_event))

        async def _cancel_all_pending(extra: asyncio.Task | None = None) -> None:
            stop_event.set()
            session_task.cancel()
            for t in monitor_tasks:
                t.cancel()
            if extra is not None:
                extra.cancel()
            await asyncio.gather(
                session_task,
                *monitor_tasks,
                return_exceptions=True,
            )

        if cfg.modules.game_entry_detect:
            if self._audit is not None:
                self._audit.log_phase(
                    PipelinePhase.GAME_ENTRY.value,
                    "开始 AI 进入游戏判定与正常退出",
                )
            detector = GameEntryDetector(
                adb=self._adb,
                app_config=cfg,
                artifact_root=self._artifact_root,
                exit_state=exit_state,
                session_state=session_state,
                audit=self._audit,
            )
            entry_task = asyncio.create_task(
                detector.run_until_normal_exit(stop_event),
            )
            pending_set: set[asyncio.Task] = {
                *monitor_tasks,
                entry_task,
                session_task,
            }
            entry_result = None
            while pending_set:
                done, _ = await asyncio.wait(
                    pending_set,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    pending_set.discard(task)
                    if task is session_task:
                        session_err = task.result()
                        if session_err:
                            await _cancel_all_pending(entry_task)
                            return f"Session restart limit: {session_err}"
                        continue
                    if task is entry_task:
                        entry_result = task.result()
                        continue
                    try:
                        anomaly = task.result()
                    except Exception as e:
                        await _cancel_all_pending(entry_task)
                        return f"Monitor task crashed during game entry: {e}"
                    if anomaly:
                        await _cancel_all_pending(entry_task)
                        return anomaly

            await _cancel_all_pending()

            assert entry_result is not None
            if not entry_result.ok:
                return f"Game entry detect failed: {entry_result.message}"

            if not exit_state.normal_exit_committed:
                return "Game entry detect succeeded but normal exit was not committed"

            logger.info(
                "进入游戏判定通过且已正常退出: %s | 会话重启=%d",
                entry_result.message[:300],
                session_state.restarts_count,
            )
            self._observer_session_restarts = session_state.restarts_count
            return None

        if not monitor_tasks:
            session_task.cancel()
            await asyncio.gather(session_task, return_exceptions=True)
            return None

        pending_set = {*monitor_tasks, session_task}
        timeout_s = cfg.game.timeout_s
        deadline = time.monotonic() + timeout_s
        timed_out = False
        while pending_set and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            done, _ = await asyncio.wait(
                pending_set,
                timeout=max(0.1, remaining),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                timed_out = True
                break
            for task in done:
                pending_set.discard(task)
                if task is session_task:
                    session_err = task.result()
                    if session_err:
                        stop_event.set()
                        for t in pending_set:
                            t.cancel()
                        return f"Session restart limit: {session_err}"
                    continue
                try:
                    result = task.result()
                    if result:
                        stop_event.set()
                        session_task.cancel()
                        for t in pending_set:
                            t.cancel()
                        return result
                except Exception as e:
                    stop_event.set()
                    session_task.cancel()
                    for t in pending_set:
                        t.cancel()
                    return f"Monitor task crashed: {e}"

        stop_event.set()
        session_task.cancel()
        for task in pending_set:
            task.cancel()
        await asyncio.gather(*pending_set, return_exceptions=True)

        if timed_out:
            logger.info("观察者监控 %s 秒无异常，视为通过", timeout_s)
        self._observer_session_restarts = session_state.restarts_count
        return None

    def _sync_task_gid_from_config(self, cfg: AppConfig) -> None:
        gid = (cfg.gameturbo.gid or "").strip()
        if not gid or gid == self._task_gid:
            return
        assert self._deliverable is not None
        old_root = self._deliverable.root
        self._task_gid = gid
        new_root = create_task_output_dir(
            cfg.gameturbo.run_outputs_dir,
            gid,
            self._task_id,
        ).root
        if new_root != old_root:
            if old_root.is_dir() and not any(old_root.iterdir()):
                old_root.rmdir()
            elif old_root.is_dir():
                logger.warning("任务产出目录 gid 已更新: %s -> %s", old_root, new_root)
            self._deliverable = create_task_output_dir(
                cfg.gameturbo.run_outputs_dir,
                gid,
                self._task_id,
            )
            logger.info("任务产出目录: %s", self._deliverable.root)

    def _resolve_source_apk(self, cfg: AppConfig) -> Path | None:
        configured = cfg.gameturbo.source_apk
        if configured is not None and configured.is_file():
            return configured.resolve()
        try:
            return discover_source_apk()
        except RuntimeError:
            return None

    def _cleanup_packages_after_attempt(self) -> None:
        with trace_operation("packages", "cleanup_deploy_artifacts_after_attempt") as rec:
            removed = cleanup_deploy_artifacts()
            rec.ok(removed=removed)
        if removed and self._audit is not None:
            self._audit.log_phase(
                "packages",
                "本轮结束，已清理 deploy 产物",
                removed=removed,
            )

    def _finalize_packages_after_deliverable(self) -> None:
        with trace_operation("packages", "finalize_after_deliverable") as rec:
            removed = cleanup_deploy_artifacts()
            deleted_source = remove_source_apk(self._source_apk_path)
            rec.ok(removed_deploy=removed, deleted_source=deleted_source)
        if removed:
            logger.info("任务结束，已清理 deploy 产物: %s", ", ".join(removed))
        if deleted_source:
            logger.info("任务结束，已删除原包")

    def _finish_run(
        self,
        *,
        success: bool,
        max_retries: int,
        winning_retry: int = 0,
        last_reason: str = "",
    ) -> int:
        assert self._deliverable is not None
        cfg = self._app_config
        assert cfg is not None

        extra_tracer = False
        if get_pipeline_tracer() is None and cfg.logging.enable_pipeline_trace:
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
    ) -> int:
        if success:
            config_path = GAMETURBO_MERGED_CONFIG_PATH
            if not config_path.is_file():
                raise RuntimeError(
                    f"测试通过但缺少 deploy 合并配置 {config_path}，"
                    "请确认 deploy.sh 已执行并生成 .gameturbo_merged.json"
                )
            winning_root = dict(self._attempt_records).get(winning_retry)
            if winning_root is None and self._attempt_records:
                winning_root = self._attempt_records[-1][1]
            if winning_root is None:
                raise RuntimeError("测试通过但缺少 artifact 目录，无法记录产出元数据")
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
            return 0

        reason = last_reason or self._last_failure_reason or "未知失败"
        ai_report = asyncio.run(
            generate_failure_diagnosis_report(
                cfg,
                gid=self._task_gid,
                task_id=self._task_id,
                last_reason=reason,
                attempt_records=self._attempt_records,
                game_config_path=cfg.gameturbo.game_config_path,
            ),
        )
        publish_failure_deliverable(
            self._deliverable,
            attempt_artifact_roots=self._attempt_records,
            last_reason=reason,
            max_retries=max_retries,
            ai_report=ai_report,
        )
        self._finalize_packages_after_deliverable()
        logger.info(
            "任务失败产出已写入: %s（含 AI 报告 failure_report.md）",
            self._deliverable.root,
        )
        return 1

    def _prepare_gameturbo_context(self, cfg: AppConfig) -> None:
        assert self._artifact_root is not None
        output_apk = output_apk_path()
        if needs_initial_preprocess():
            if self._audit is not None:
                self._audit.log_phase(
                    PipelinePhase.INIT.value,
                    "进入 GameTurbo 前置处理",
                    output_apk=str(output_apk),
                )
            result = run_bootstrap(self._config_path)
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
            deploy_result = run_deploy_with_ai_retry_sync(
                cfg,
                self._config_path,
                gid=result.gid,
                game_config_path=result.game_config_path,
                artifact_root=self._artifact_root,
                audit=self._audit,
                phase=PipelinePhase.INIT.value,
            )
            if self._audit is not None:
                self._audit.log_phase(
                    PipelinePhase.INIT.value,
                    "GameTurbo deploy 已完成",
                    gid=result.gid,
                    deploy_log=str(deploy_result.log_path or ""),
                    output_apk=str(output_apk),
                )
            return

        if cfg.gameturbo.gid and cfg.gameturbo.game_config_path:
            logger.info(
                "跳过 GameTurbo Init，使用已有上下文 gid=%s config=%s",
                cfg.gameturbo.gid,
                cfg.gameturbo.game_config_path,
            )
            if self._audit is not None:
                self._audit.log_phase(
                    PipelinePhase.INIT.value,
                    "跳过前置处理，已有 game_gameturbo.apk",
                    gid=cfg.gameturbo.gid,
                    game_config_path=str(cfg.gameturbo.game_config_path),
                    output_apk=str(output_apk),
                )
            return

        source_apk = discover_source_apk()
        gid = parse_gid_from_apk_name(source_apk)
        game_config_path = resolve_existing_game_config(gid)
        persist_gameturbo_context(
            self._config_path,
            gid=gid,
            game_config_path=game_config_path,
            source_apk=source_apk,
        )
        logger.info(
            "已从现有 game_gameturbo.apk 恢复 GameTurbo 上下文: gid=%s config=%s",
            gid,
            game_config_path,
        )
        if self._audit is not None:
            self._audit.log_phase(
                PipelinePhase.INIT.value,
                "恢复 GameTurbo 上下文并跳过前置处理",
                gid=gid,
                game_config_path=str(game_config_path),
                source_apk=str(source_apk),
                output_apk=str(output_apk),
            )

    def _archive_gameturbo_log(self) -> None:
        assert self._adb is not None
        if self._artifact_root is None:
            return
        finalize_gameturbo_log(self._adb, self._artifact_root)

    def _write_attempt_failure_report_sync(
        self,
        cfg: AppConfig,
        retry_count: int,
        reason: str,
        *,
        will_retry: bool,
    ) -> None:
        if self._artifact_root is None:
            return
        gid = (self._task_gid or cfg.gameturbo.gid or "").strip() or "unknown"
        try:
            asyncio.run(
                generate_and_save_attempt_failure_report(
                    cfg,
                    retry_no=retry_count,
                    artifact_root=self._artifact_root,
                    reason=reason,
                    gid=gid,
                    will_retry=will_retry,
                    game_config_path=cfg.gameturbo.game_config_path,
                ),
            )
        except Exception as e:
            logger.warning("本轮 AI 失败报告生成失败: %s", e)

    def _handle_failure_sync(
        self,
        retry_count: int,
        reason: str,
        *,
        run_retry_config: bool,
        max_retries: int,
    ) -> None:
        assert self._adb is not None
        assert self._app_config is not None
        handler = AnomalyHandler(
            adb=self._adb,
            app_config=self._app_config,
            config_path=self._config_path,
            artifact_root=self._artifact_root,
            audit=self._audit,
        )
        self._last_failure_reason = reason
        will_retry = run_retry_config and retry_count < max_retries
        asyncio.run(
            handler.handle(
                retry_count,
                reason,
                run_retry_config=run_retry_config,
                will_retry=will_retry,
            ),
        )


def run_orchestrator(config_path: Path) -> int:
    return GameTestOrchestrator(config_path).run()
