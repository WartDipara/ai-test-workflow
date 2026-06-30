"""游戏进程重启后的 Launch 状态重置与分场景恢复。"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from game_agent.graphs.launch_phase import clear_game_entry_judgment, is_login_active
from game_agent.graphs.launch_state_store import (
    clear_failed_node,
    completed_tree_node,
    is_login_done,
    reset_login_progress,
)
from game_agent.models.launch_graph_state import LaunchFacts, LaunchGraphState, facts_from_state
from game_agent.services.scene_strategies import clear_scene_strategy
from game_agent.services.session_agent import activate_session_agent

logger = logging.getLogger(__name__)

SessionRestartPhase = Literal["pre_login", "during_login", "post_login_in_game"]

_LOGIN_PIPELINE_ACTIONS = (
    "handle_initial_privacy_dialog",
    "ensure_privacy_checkbox",
    "handle_download",
    "dismiss_blocking_overlay",
    "atomic_login",
    "select_sub_account",
    "check_server_selector",
    "tap_enter_game",
)


def deactivate_session_agent(state: LaunchGraphState) -> None:
    state["session_agent_active"] = False
    state["session_agent_started_at"] = 0.0


def clear_in_game_session_scratch(state: LaunchGraphState) -> None:
    """保留 in_game_agent_rounds 等历史计数，仅清本轮可执行状态。"""
    state["in_game_entry_passed"] = False
    state["in_game_agent_done"] = False
    state["in_game_play_completed"] = False
    state["in_game_vlm_no_progress_streak"] = 0
    state["in_game_behavior_no_progress"] = 0
    state["last_in_game_screen_analysis"] = {}
    state["last_motion_summary"] = ""
    state["last_spatial_hints"] = ""
    state["scene_label_fast_path"] = False


def _was_in_game_session(state: LaunchGraphState) -> bool:
    return bool(
        state.get("session_agent_active")
        or int(state.get("in_game_agent_rounds") or 0) > 0
        or state.get("in_game_entry_passed")
        or int(state.get("enter_tapped_count") or 0) >= 1
    )


def _login_flow_started(state: LaunchGraphState) -> bool:
    if state.get("account_filled") or state.get("password_filled") or state.get("login_submitted"):
        return True
    for action in ("atomic_login", "select_sub_account", "tap_enter_game"):
        if completed_tree_node(state, action):
            return True
    if int(state.get("free_rounds") or 0) > 0:
        return True
    if int(state.get("adaptive_rounds") or 0) > 0:
        return True
    if state.get("dynamic_chain"):
        return True
    stage = str(state.get("current_stage") or "")
    if stage in (
        "login_form",
        "sub_account_select",
        "session_relogin",
        "download",
        "server_select",
        "adaptive_phase",
        "free",
    ):
        return True
    return False


def classify_session_restart_phase(state: LaunchGraphState) -> SessionRestartPhase:
    """
    三档重启场景：
    - pre_login：尚未进入登录流，照旧 DFS
    - during_login：登录进行中（含已 login_done 但未进游戏），保留进度 + VLM 续跑
    - post_login_in_game：已在局内，简化重登
    """
    if is_login_done(state) and _was_in_game_session(state):
        return "post_login_in_game"
    if _login_flow_started(state):
        return "during_login"
    if is_login_done(state) and not _was_in_game_session(state):
        return "during_login"
    return "pre_login"


def capture_restart_checkpoint(state: LaunchGraphState) -> dict[str, Any]:
    """记录崩溃前进度，供 during_login VLM 续跑参考。"""
    completed = state.get("completed_nodes") or {}
    done_keys = [k for k, v in completed.items() if isinstance(v, dict) and v.get("done")]
    return {
        "current_stage": str(state.get("current_stage") or ""),
        "current_tree_node": str(state.get("current_tree_node") or ""),
        "tree_trace": str(state.get("tree_trace") or ""),
        "last_route": str(state.get("last_route") or ""),
        "account_filled": bool(state.get("account_filled")),
        "password_filled": bool(state.get("password_filled")),
        "login_submitted": bool(state.get("login_submitted")),
        "login_done": bool(state.get("login_done")),
        "completed_node_keys": done_keys[-12:],
        "dynamic_cursor": int(state.get("dynamic_cursor") or 0),
        "dynamic_chain_len": len(state.get("dynamic_chain") or []),
        "action_failure_trace": list(state.get("action_failure_trace") or [])[-5:],
        "dynamic_failure_trace": list(state.get("dynamic_failure_trace") or [])[-5:],
        "free_rounds": int(state.get("free_rounds") or 0),
        "adaptive_rounds": int(state.get("adaptive_rounds") or 0),
    }


def _clear_login_pipeline_failures(state: LaunchGraphState) -> None:
    for action in _LOGIN_PIPELINE_ACTIONS:
        clear_failed_node(state, action)


def _arm_recovery_node(
    state: LaunchGraphState,
    *,
    evidence: str,
    session_index: int,
) -> None:
    state["session_relogin_recovery_active"] = True
    state["session_relogin_rounds"] = 0
    state["session_relogin_started_at"] = time.monotonic()
    if session_index > 0:
        state["session_relogin_session_index"] = session_index
    state["recover_hint"] = (evidence or "session_restart")[:500]


def clear_stale_scene_classify_scratch(state: LaunchGraphState) -> None:
    """闪退后丢弃进行中的 scene/VLM 分类残留。"""
    from game_agent.services.scene_strategies import clear_scene_strategy

    clear_scene_strategy(state)
    state["scene_id"] = "unknown"
    state["scene_confidence"] = 0.0
    state["scene_evidence"] = ""
    state["scene_fingerprint"] = ""
    state["scene_strategy_active"] = False
    state["scene_transition"] = ""
    state["scene_transition_reason"] = ""
    state["interpret_screenshot_hash"] = ""
    state["scene_gate_screenshot_hash"] = ""
    state["scene_gate_scene_id"] = ""
    state["scene_gate_confidence"] = 0.0
    state["scene_gate_description"] = ""
    state["pending_vision_path"] = ""
    state["vision_enrichment_status"] = ""
    state["scene_label_fast_path"] = False
    state["last_scene_label_judgment"] = {}


def apply_session_restart_to_state(
    state: LaunchGraphState,
    *,
    evidence: str = "",
    session_index: int = 0,
) -> SessionRestartPhase:
    phase = classify_session_restart_phase(state)
    state["session_restart_phase"] = phase
    state["session_restart_checkpoint"] = capture_restart_checkpoint(state)

    deactivate_session_agent(state)
    clear_scene_strategy(state)
    clear_stale_scene_classify_scratch(state)

    if session_index > 0:
        state["session_relogin_session_index"] = session_index

    if phase == "pre_login":
        clear_in_game_session_scratch(state)
        state["session_relogin_recovery_active"] = False
        logger.warning(
            "[SessionRestart] phase=pre_login | resume normal DFS | evidence=%s",
            (evidence or "")[:120],
        )
        return phase

    if phase == "during_login":
        clear_in_game_session_scratch(state)
        _clear_login_pipeline_failures(state)
        state["interpret_screenshot_hash"] = ""
        _arm_recovery_node(
            state,
            evidence=evidence or "session_restart:during_login_resume",
            session_index=session_index,
        )
        logger.warning(
            "[SessionRestart] phase=during_login | preserve login flags | "
            "account=%s password=%s submitted=%s login_done=%s tree=%s",
            state.get("account_filled"),
            state.get("password_filled"),
            state.get("login_submitted"),
            state.get("login_done"),
            (state.get("tree_trace") or "")[:80],
        )
        return phase

    reset_login_progress(
        state,
        evidence=evidence or "session_restart:post_login_relogin",
    )
    clear_in_game_session_scratch(state)
    clear_game_entry_judgment(state)
    _arm_recovery_node(
        state,
        evidence=evidence or "session_restart:post_login_relogin",
        session_index=session_index,
    )
    logger.warning(
        "[SessionRestart] phase=post_login_in_game | simplified relogin | login_done=False",
    )
    return phase


def maybe_complete_session_relogin_recovery(
    state: LaunchGraphState,
    facts: LaunchFacts | None = None,
) -> bool:
    """重登/续登完成：已 login_done 且离开登录页。"""
    if not state.get("session_relogin_recovery_active"):
        return False
    if not is_login_done(state):
        return False
    facts = facts or facts_from_state(state)
    if is_login_active(state, facts):
        return False

    phase = str(state.get("session_restart_phase") or "")
    state["session_relogin_recovery_active"] = False
    state["session_restart_phase"] = ""

    if phase == "post_login_in_game":
        had_play = int(state.get("in_game_agent_rounds") or 0) > 0
        had_enter = int(state.get("enter_tapped_count") or 0) >= 1
        if had_play or had_enter or state.get("in_game_entry_passed"):
            activate_session_agent(state, reason="session_restart_recovery_complete")
            logger.info(
                "[SessionRestart] post_login recovery complete → session_agent "
                "(rounds=%d enter_taps=%d)",
                int(state.get("in_game_agent_rounds") or 0),
                int(state.get("enter_tapped_count") or 0),
            )
        else:
            logger.info("[SessionRestart] post_login recovery complete → DFS resume")
    else:
        logger.info("[SessionRestart] during_login recovery complete → DFS resume")

    return True


def session_relogin_recovery_active(state: LaunchGraphState) -> bool:
    return bool(state.get("session_relogin_recovery_active"))


def format_checkpoint_for_prompt(checkpoint: dict[str, Any] | None) -> str:
    if not checkpoint:
        return "none"
    lines = [
        f"stage={checkpoint.get('current_stage', '')}",
        f"tree={checkpoint.get('tree_trace', '')}",
        f"last_route={checkpoint.get('last_route', '')}",
        f"account_filled={checkpoint.get('account_filled')}",
        f"password_filled={checkpoint.get('password_filled')}",
        f"login_submitted={checkpoint.get('login_submitted')}",
        f"login_done={checkpoint.get('login_done')}",
        f"completed={','.join(checkpoint.get('completed_node_keys') or [])}",
        f"dynamic_step={checkpoint.get('dynamic_cursor')}/{checkpoint.get('dynamic_chain_len')}",
    ]
    traces = checkpoint.get("action_failure_trace") or checkpoint.get("dynamic_failure_trace")
    if traces:
        last = traces[-1] if isinstance(traces, list) else {}
        if isinstance(last, dict):
            lines.append(
                f"last_failure={last.get('reason', '')[:120]} "
                f"step={last.get('step_id') or last.get('label', '')}"
            )
    return "\n".join(lines)
