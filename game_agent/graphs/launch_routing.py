"""LangGraph 路由：DFS 状态树 + 动态子树链 + free 兜底。"""

from __future__ import annotations

import logging
import re

from game_agent.graphs.launch_tree import TreeTrace, launch_dfs_next
from game_agent.graphs.launch_limits import launch_graph_limits_from_state
from game_agent.graphs.launch_phase import (
    is_login_active,
    is_post_login,
    is_pre_login_scene_allowed,
    reconcile_action_frames,
    reconcile_login_state,
)
from game_agent.graphs.launch_state_store import (
    is_privacy_checked,
    node_attempts,
)
from game_agent.graphs.state_tree import StateTreeDecision
from game_agent.models.launch_graph_state import (
    LaunchFacts,
    LaunchGraphState,
    LaunchRouteTarget,
    facts_from_state,
)
from game_agent.graphs.static_priority import blocks_scene_routing
from game_agent.services.dynamic_route_planner import has_active_dynamic_chain
from game_agent.models.scene import SCENE_STRATEGY_IDS
from game_agent.services.scene_strategies import is_pre_login_passive_wait

logger = logging.getLogger(__name__)

_CHARACTER_ROUTE_RE = re.compile(
    r"创角|创建角色|选择职业|Click\s*to\s*Create|Create\s*Role|Enter\s*World|进入世界",
    re.IGNORECASE,
)

_STATIC_BLOCKING_ACTIONS: frozenset[LaunchRouteTarget] = frozenset(
    {
        "handle_initial_privacy_dialog",
        "ensure_privacy_checkbox",
        "handle_download",
        "dismiss_blocking_overlay",
        "atomic_login",
        "select_sub_account",
        "check_server_selector",
    },
)


def _check_in_game_failed(state: LaunchGraphState) -> bool:
    failed = state.get("failed_nodes") or {}
    return "enter.check_in_game" in failed or "check_in_game" in failed


def _adaptive_phase_failed(state: LaunchGraphState) -> bool:
    failed = state.get("failed_nodes") or {}
    bucket = failed.get("adaptive_phase")
    return bool(isinstance(bucket, dict) and bucket.get("failed"))


def should_route_scene(state: LaunchGraphState, facts: LaunchFacts) -> bool:
    """场景策略：对话/教程/加载等连续推进，优先于 adaptive/dynamic/free。"""
    if state.get("in_game_confirmed"):
        return False
    if state.get("in_game_entry_passed"):
        return False
    if facts.sub_account_blocking:
        return False
    if facts.initial_privacy_dialog:
        return False
    if blocks_scene_routing(state, facts):
        return False
    if is_login_active(state, facts):
        return False
    scene_id = str(state.get("scene_id") or facts.scene_id or "")
    confidence = float(state.get("scene_confidence") or facts.scene_confidence or 0)
    if is_pre_login_passive_wait(
        state,
        facts,
        scene_id=scene_id,
        confidence=confidence,
    ):
        return True
    if is_pre_login_scene_allowed(
        state,
        facts,
        scene_id=scene_id,
        confidence=confidence,
    ):
        return True
    if not is_post_login(state, facts):
        return False
    if state.get("scene_strategy_active"):
        active = str(state.get("active_scene_strategy") or state.get("scene_id") or "")
        if active in SCENE_STRATEGY_IDS:
            return True
    if scene_id in SCENE_STRATEGY_IDS and confidence >= 0.55:
        return True
    return False


def should_route_adaptive(state: LaunchGraphState, facts: LaunchFacts) -> bool:
    """登录后可变 UI：由 AI 阶段模板处理，优先于 dynamic/check_in_game。"""
    if should_route_scene(state, facts):
        return False
    if not is_post_login(state, facts):
        return False
    if is_login_active(state, facts):
        return False
    if state.get("in_game_confirmed"):
        return False
    if state.get("in_game_entry_passed"):
        return False
    if state.get("adaptive_flow_done"):
        return False
    limits = launch_graph_limits_from_state(state)
    if int(state.get("adaptive_rounds") or 0) >= limits.max_adaptive_rounds:
        return False
    if _adaptive_phase_failed(state):
        return False
    if facts.login_blocking or facts.sub_account_blocking:
        return False
    if facts.initial_privacy_dialog:
        return False
    if state.get("adaptive_active_node_id"):
        return True
    if state.get("current_phase_spec"):
        return True
    if _check_in_game_failed(state):
        return True
    if facts.character_creation_blocking:
        return True
    if facts.interpreter_stage in ("character_creation", "unknown"):
        return True
    return False


def should_route_dynamic(state: LaunchGraphState) -> bool:
    """动态链仍有待执行步骤。"""
    limits = launch_graph_limits_from_state(state)
    facts = facts_from_state(state)
    if not is_post_login(state, facts):
        return False
    if is_login_active(state, facts):
        return False
    if should_route_scene(state, facts):
        return False
    if state.get("in_game_entry_passed") and not state.get("in_game_confirmed"):
        return False
    if state.get("dynamic_failed"):
        return False
    if int(state.get("dynamic_rounds") or 0) >= limits.max_dynamic_rounds:
        return False
    if int(state.get("dynamic_no_progress") or 0) >= limits.max_dynamic_no_progress:
        return False
    return has_active_dynamic_chain(state)


def should_route_free(
    state: LaunchGraphState,
    facts: LaunchFacts,
    decision: StateTreeDecision[LaunchRouteTarget],
) -> bool:
    """登录完成后、仍未进游戏时，是否进入 free 兜底节点。"""
    limits = launch_graph_limits_from_state(state)
    if not is_post_login(state, facts):
        return False
    if is_login_active(state, facts):
        return False
    if state.get("in_game_confirmed"):
        return False
    if state.get("in_game_entry_passed"):
        return False
    if should_route_adaptive(state, facts):
        return False
    if should_route_scene(state, facts):
        return False
    if should_route_dynamic(state):
        return False
    if int(state.get("free_rounds") or 0) >= limits.max_free_rounds:
        return False
    if int(state.get("free_no_progress_rounds") or 0) >= limits.max_free_no_progress_rounds:
        return False
    if facts.login_blocking or facts.sub_account_blocking:
        return False
    if facts.initial_privacy_dialog:
        return False
    if facts.terms_checkbox_visible and not is_privacy_checked(state):
        return False

    ocr = str(state.get("last_ocr_summary") or "")
    char_hint = bool(_CHARACTER_ROUTE_RE.search(ocr))

    if facts.character_creation_blocking:
        return True
    if facts.vision_stage == "character_creation":
        return True
    if facts.interpreter_stage == "character_creation":
        return True
    if char_hint:
        return True
    if _check_in_game_failed(state):
        return True
    if decision.action is None and not facts.download_visible:
        return True
    return False


def plan_route(state: LaunchGraphState) -> LaunchRouteTarget:
    """
    计算下一节点并写入 state（须在节点返回值中调用，勿在 LangGraph 条件边回调里改 state）。
    """
    if state.get("finished") or state.get("terminal_error"):
        target: LaunchRouteTarget = "end"
    elif state.get("in_game_confirmed"):
        target = "end"
    else:
        limits = launch_graph_limits_from_state(state)
        logger.info(
            "[LaunchGraph:route] login_flags account_filled=%s password_filled=%s "
            "login_submitted=%s login_done=%s",
            state.get("account_filled"),
            state.get("password_filled"),
            state.get("login_submitted"),
            state.get("login_done"),
        )
        facts = facts_from_state(state)
        reconcile_login_state(state, facts)
        facts = reconcile_action_frames(state, facts)
        trace = TreeTrace()
        decision = launch_dfs_next(state, facts, trace=trace)
        state["current_tree_node"] = decision.node_id
        state["tree_trace"] = "->".join(trace.visited[-12:]) if trace.visited else decision.reason

        dfs_action = decision.action
        if dfs_action in _STATIC_BLOCKING_ACTIONS:
            target = dfs_action
        elif should_route_scene(state, facts):
            target = "scene_action"
            logger.info(
                "[LaunchGraph:route] scene_enter id=%s conf=%.2f strategy=%s",
                state.get("scene_id"),
                float(state.get("scene_confidence") or 0),
                state.get("active_scene_strategy"),
            )
        elif should_route_dynamic(state):
            target = "dynamic_action"
            logger.info(
                "[LaunchGraph:route] behavior_chain_enter cursor=%d rounds=%d replans=%d",
                int(state.get("dynamic_cursor") or 0),
                int(state.get("dynamic_rounds") or 0),
                int(state.get("dynamic_replan_count") or 0),
            )
        elif should_route_adaptive(state, facts):
            target = "adaptive_phase"
            logger.info(
                "[LaunchGraph:route] adaptive_enter rounds=%d registry=%d",
                int(state.get("adaptive_rounds") or 0),
                len(state.get("phase_registry") or []),
            )
        elif dfs_action == "check_in_game":
            # Keep original convergence loop: after login/static steps, always re-check in-game.
            # Dynamic chain is inserted before this branch via should_route_dynamic.
            target = "check_in_game"
        elif dfs_action is not None:
            target = dfs_action
        elif should_route_free(state, facts, decision):
            target = "free"
            logger.info(
                "[LaunchGraph:route] free_enter tree=%s reason=%s free_rounds=%d",
                decision.node_id,
                decision.reason[:120],
                int(state.get("free_rounds") or 0),
            )
        elif node_attempts(state, "recover_from_failure") >= limits.max_node_attempts:
            target = "end"
        else:
            target = "recover_from_failure"

    state["last_route"] = target
    state["planned_next_route"] = target
    return target


def route_next(state: LaunchGraphState) -> LaunchRouteTarget:
    """计算并写入路由元数据（测试与单步调试；LangGraph 边用 consume_planned_route）。"""
    return plan_route(state)


def consume_planned_route(state: LaunchGraphState) -> LaunchRouteTarget:
    """LangGraph 条件边：读取 classify 写入的 planned_next_route。"""
    target = state.pop("planned_next_route", None)
    if target:
        return target  # type: ignore[return-value]
    return plan_route(state)
