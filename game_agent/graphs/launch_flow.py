"""LangGraph 进入游戏主图：构建、运行、同步 RunState。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from game_agent.graphs.launch_deps import LaunchGraphDeps
from game_agent.graphs.vision_enrichment import VisionEnrichmentQueue
from game_agent.graphs.launch_nodes import (
    adaptive_phase_node,
    atomic_login_node,
    check_in_game_node,
    check_server_selector_node,
    classify_screen,
    ensure_privacy_checkbox_node,
    handle_download_node,
    handle_initial_privacy_dialog_node,
    dismiss_blocking_overlay_node,
    observe_screen,
    recover_from_failure_node,
    select_sub_account_node,
    stability_observe_node,
    tap_enter_game_node,
    free_node,
    dynamic_action_node,
)
from game_agent.graphs.launch_routing import consume_planned_route
from game_agent.graphs.launch_limits import seed_launch_graph_limits
from game_agent.models.launch_graph_state import LaunchGraphState, empty_launch_graph_state
from game_agent.models.run_state import RunState
from game_agent.models.settings import AppConfig
from game_agent.modules.run_context import AttemptContext
from game_agent.services.adb_service import AdbService
from game_agent.services.package_install import wait_for_package_installed
from game_agent.services.run_audit_log import RunAuditLogger

logger = logging.getLogger(__name__)

_ACTION_NODES = (
    "handle_initial_privacy_dialog",
    "ensure_privacy_checkbox",
    "handle_download",
    "dismiss_blocking_overlay",
    "atomic_login",
    "select_sub_account",
    "check_server_selector",
    "tap_enter_game",
    "check_in_game",
    "stability_observe",
    "adaptive_phase",
    "dynamic_action",
    "free",
    "recover_from_failure",
)


def build_launch_graph(deps: LaunchGraphDeps) -> Any:
    """构建带 deps 闭包的 LangGraph。"""

    async def _observe(state: LaunchGraphState) -> LaunchGraphState:
        return await observe_screen(state, deps)

    async def _classify(state: LaunchGraphState) -> LaunchGraphState:
        return await classify_screen(state, deps)

    async def _initial_privacy(state: LaunchGraphState) -> LaunchGraphState:
        return await handle_initial_privacy_dialog_node(state, deps)

    async def _checkbox(state: LaunchGraphState) -> LaunchGraphState:
        return await ensure_privacy_checkbox_node(state, deps)

    async def _download(state: LaunchGraphState) -> LaunchGraphState:
        return await handle_download_node(state, deps)

    async def _dismiss_overlay(state: LaunchGraphState) -> LaunchGraphState:
        return await dismiss_blocking_overlay_node(state, deps)

    async def _atomic_login(state: LaunchGraphState) -> LaunchGraphState:
        return await atomic_login_node(state, deps)

    async def _sub_account(state: LaunchGraphState) -> LaunchGraphState:
        return await select_sub_account_node(state, deps)

    async def _server(state: LaunchGraphState) -> LaunchGraphState:
        return await check_server_selector_node(state, deps)

    async def _enter(state: LaunchGraphState) -> LaunchGraphState:
        return await tap_enter_game_node(state, deps)

    async def _in_game(state: LaunchGraphState) -> LaunchGraphState:
        return await check_in_game_node(state, deps)

    async def _stability(state: LaunchGraphState) -> LaunchGraphState:
        return await stability_observe_node(state, deps)

    async def _adaptive(state: LaunchGraphState) -> LaunchGraphState:
        return await adaptive_phase_node(state, deps)

    async def _recover(state: LaunchGraphState) -> LaunchGraphState:
        return await recover_from_failure_node(state, deps)

    async def _free(state: LaunchGraphState) -> LaunchGraphState:
        return await free_node(state, deps)

    async def _dynamic(state: LaunchGraphState) -> LaunchGraphState:
        return await dynamic_action_node(state, deps)

    def _route(state: LaunchGraphState) -> str:
        target = consume_planned_route(state)
        if target == "end":
            return "end"
        return target

    workflow: StateGraph = StateGraph(LaunchGraphState)
    workflow.add_node("observe_screen", _observe)
    workflow.add_node("classify_screen", _classify)
    workflow.add_node("handle_initial_privacy_dialog", _initial_privacy)
    workflow.add_node("ensure_privacy_checkbox", _checkbox)
    workflow.add_node("handle_download", _download)
    workflow.add_node("dismiss_blocking_overlay", _dismiss_overlay)
    workflow.add_node("atomic_login", _atomic_login)
    workflow.add_node("select_sub_account", _sub_account)
    workflow.add_node("check_server_selector", _server)
    workflow.add_node("tap_enter_game", _enter)
    workflow.add_node("check_in_game", _in_game)
    workflow.add_node("stability_observe", _stability)
    workflow.add_node("adaptive_phase", _adaptive)
    workflow.add_node("dynamic_action", _dynamic)
    workflow.add_node("free", _free)
    workflow.add_node("recover_from_failure", _recover)

    workflow.set_entry_point("observe_screen")
    workflow.add_edge("observe_screen", "classify_screen")
    route_map = {name: name for name in _ACTION_NODES}
    route_map["end"] = END
    workflow.add_conditional_edges("classify_screen", _route, route_map)

    def _after_atomic_login(state: LaunchGraphState) -> str:
        if state.get("login_done"):
            logger.info(
                "[LaunchGraph:route] atomic_login → classify_screen (skip observe)"
            )
            return "classify_screen"
        return "observe_screen"

    workflow.add_conditional_edges(
        "atomic_login",
        _after_atomic_login,
        {"classify_screen": "classify_screen", "observe_screen": "observe_screen"},
    )
    for name in _ACTION_NODES:
        if name != "atomic_login":
            workflow.add_edge(name, "observe_screen")

    return workflow.compile()


def _sync_run_state_from_graph(run_state: RunState, graph_state: LaunchGraphState) -> None:
    run_state.privacy_checkbox_tapped = bool(graph_state.get("privacy_checked"))
    run_state.server_checked = bool(graph_state.get("server_checked"))
    run_state.in_game_confirmed = bool(graph_state.get("in_game_confirmed"))
    run_state.finished = bool(graph_state.get("finished")) or bool(graph_state.get("terminal_error"))
    run_state.success = bool(graph_state.get("in_game_confirmed"))
    if graph_state.get("terminal_error"):
        run_state.note = str(graph_state["terminal_error"])[:2000]
        run_state.last_error = run_state.note
    run_state.launch_stage = str(graph_state.get("current_stage") or run_state.launch_stage)
    run_state.graph_state_snapshot = dict(graph_state)


async def _bootstrap_game(deps: LaunchGraphDeps) -> str | None:
    """包安装等待 + 打开游戏（与 legacy executor 开局一致）。"""
    cfg = deps.app_config
    pkg = cfg.game.package_name.strip()
    if not pkg:
        return "game.package_name empty"
    if not deps.run_state.package_install_confirmed:
        result = await asyncio.to_thread(
            wait_for_package_installed,
            deps.adb,
            pkg,
            timeout_s=cfg.game.package_install_wait_timeout_s,
            poll_interval_s=cfg.game.package_install_poll_interval_s,
            should_abort=lambda: (
                deps.attempt_context is not None and deps.attempt_context.should_stop_executor()
            ),
        )
        if not result.ok:
            return result.to_tool_message()
        deps.run_state.package_install_confirmed = True
    activity = cfg.game.launch_activity.strip()
    if not activity:
        return "Config error: game.launch_activity is empty"
    try:
        deps.adb.launch_game(pkg, activity)
    except Exception as e:
        logger.warning("launch_game failed (graph will continue): %s", e)
    deps.adb.wait_seconds(cfg.executor.post_launch_wait_s)
    return None


async def run_launch_graph_async(
    *,
    app_config: AppConfig,
    adb: AdbService,
    run_state: RunState,
    artifact_root: Path,
    settings_path: Path,
    audit: RunAuditLogger | None = None,
    attempt_context: AttemptContext | None = None,
    screen_width: int = 0,
    screen_height: int = 0,
) -> RunState:
    executor_art = artifact_root / "executor"
    executor_art.mkdir(parents=True, exist_ok=True)
    vision_queue = VisionEnrichmentQueue(
        llm_cfg=app_config.llm_multimodal,
        round_id=0,
    )
    deps = LaunchGraphDeps(
        app_config=app_config,
        adb=adb,
        run_state=run_state,
        artifact_root=executor_art,
        settings_path=settings_path,
        audit=audit,
        attempt_context=attempt_context,
        vision_queue=vision_queue,
        screen_width=screen_width,
        screen_height=screen_height,
    )
    bootstrap_err = await _bootstrap_game(deps)
    if bootstrap_err:
        run_state.finished = True
        run_state.success = False
        run_state.note = bootstrap_err[:2000]
        return run_state

    graph = build_launch_graph(deps)
    state: LaunchGraphState = empty_launch_graph_state()
    seed_launch_graph_limits(state, app_config)
    state["privacy_checked"] = run_state.privacy_checkbox_tapped
    state["server_checked"] = run_state.server_checked

    if attempt_context is not None and attempt_context.should_stop_executor():
        reason = attempt_context.get_fatal_reason() or "monitor stop"
        run_state.finished = True
        run_state.success = False
        run_state.note = reason[:2000]
        return run_state

    try:
        state = await graph.ainvoke(state)
    finally:
        await vision_queue.shutdown()
    _sync_run_state_from_graph(run_state, state)

    if not run_state.in_game_confirmed and not run_state.finished:
        run_state.finished = True
        run_state.success = False
        err = state.get("terminal_error") or state.get("recover_hint") or "launch graph iteration limit"
        run_state.note = str(err)[:2000]

    return run_state


def run_launch_graph_sync(
    *,
    app_config: AppConfig,
    adb: AdbService,
    run_state: RunState,
    artifact_root: Path,
    settings_path: Path,
    audit: RunAuditLogger | None = None,
    attempt_context: AttemptContext | None = None,
) -> RunState:
    return asyncio.run(
        run_launch_graph_async(
            app_config=app_config,
            adb=adb,
            run_state=run_state,
            artifact_root=artifact_root,
            settings_path=settings_path,
            audit=audit,
            attempt_context=attempt_context,
        ),
    )
