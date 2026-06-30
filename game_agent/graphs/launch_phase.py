"""进游戏前阶段感知：分层门禁（非全局里程碑锁）。"""

from __future__ import annotations

from game_agent.graphs.launch_state_store import (
    clear_completed_node,
    completed_tree_node,
    is_login_done,
    is_sub_account_selected,
    reset_login_progress,
)
from game_agent.graphs.static_priority import has_pending_static_work
from game_agent.models.launch_graph_state import LaunchFacts, LaunchGraphState
from game_agent.services.login_stage_probe import split_screen_login_active_reason

_PRE_LOGIN_SCENE_IDS = frozenset({"dialogue", "tutorial", "loading"})
_VLM_LOGIN_STAGES = frozenset({"login"})
_VLM_LOGIN_BLOCKERS = frozenset({"login"})
_VLM_POST_LOGIN_STAGES = frozenset(
    {
        "sub_account_select",
        "sub_account",
        "resource_download",
        "server_select",
        "enter_game",
        "character_creation",
        "character_select",
    },
)


def _split_screen_login_active(
    facts: LaunchFacts,
    state: LaunchGraphState,
) -> bool:
    if split_screen_login_active_reason(facts.classify_reason):
        return True
    if state.get("split_screen_login"):
        return True
    return False


def _vlm_indicates_login_screen(
    state: LaunchGraphState,
    facts: LaunchFacts | None = None,
) -> bool:
    judgment = state.get("last_game_entry_judgment")
    if not isinstance(judgment, dict):
        return False
    stage = str(judgment.get("stage") or "").strip().lower()
    if stage in _VLM_POST_LOGIN_STAGES:
        return False
    if facts is not None:
        if facts.login_stage in ("clear", "sub_account_select") and not facts.login_blocking:
            return False
        if facts.enter_cta_visible and facts.login_stage != "login_form":
            if not facts.login_blocking and not _split_screen_login_active(facts, state):
                return False
    if stage in _VLM_LOGIN_STAGES:
        return True
    blockers = judgment.get("blockers") or []
    if isinstance(blockers, list):
        for blocker in blockers:
            token = str(blocker).strip().lower()
            if token == "login_screen":
                continue
            if token in _VLM_LOGIN_BLOCKERS:
                return True
    return False


def is_login_active(state: LaunchGraphState, facts: LaunchFacts) -> bool:
    """登录页或登录进行中（实时画面信号）。"""
    if facts.sub_account_blocking:
        return False
    if is_sub_account_selected(state):
        return False
    if completed_tree_node(state, "select_sub_account"):
        return False
    if facts.login_stage == "login_form":
        return True
    if facts.login_blocking:
        return True
    if facts.login_stage in ("clear", "sub_account_select") and not facts.login_blocking:
        return False
    if facts.enter_cta_visible and facts.login_stage != "login_form":
        if not facts.login_blocking and not _split_screen_login_active(facts, state):
            return False
    if _vlm_indicates_login_screen(state, facts):
        return True
    if not is_login_done(state):
        if state.get("account_filled") or state.get("password_filled"):
            return True
    return False


def ocr_credential_login_passed(*, left_login_form: bool, stage: str) -> bool:
    """OCR 已离开登录表单即视为凭证登录成功（与进游戏门解耦）。"""
    if not left_login_form:
        return False
    return stage != "login_form"


def is_post_login(state: LaunchGraphState, facts: LaunchFacts) -> bool:
    return is_login_done(state) and not is_login_active(state, facts)


def is_pre_login_scene_allowed(
    state: LaunchGraphState,
    facts: LaunchFacts,
    *,
    scene_id: str,
    confidence: float,
) -> bool:
    """登录前开场 CG/对话/加载：无 login_form 时可走 scene。"""
    if is_login_done(state):
        return False
    if is_login_active(state, facts):
        return False
    if has_pending_static_work(state, facts):
        return False
    if scene_id not in _PRE_LOGIN_SCENE_IDS:
        return False
    return confidence >= 0.55


def in_game_entry_allowed(state: LaunchGraphState, facts: LaunchFacts) -> bool:
    """HUD 捷径 / in_game_entry_passed 前置。"""
    if not is_post_login(state, facts):
        return False
    if is_login_active(state, facts):
        return False
    if int(state.get("enter_tapped_count") or 0) >= 1:
        return True
    if bool(state.get("adaptive_flow_done")):
        return True
    if completed_tree_node(state, "check_in_game"):
        return True
    return False


def reconcile_login_state(state: LaunchGraphState, facts: LaunchFacts) -> None:
    """修正 login_done / completed_nodes / in_game_entry 与画面矛盾。"""
    if is_login_done(state) and is_login_active(state, facts):
        reset_login_progress(
            state,
            evidence="phase:login_screen_visible_after_login_done",
        )
        return

    if completed_tree_node(state, "atomic_login") and not is_login_done(state):
        clear_completed_node(state, "atomic_login")

    if state.get("in_game_entry_passed") and not in_game_entry_allowed(state, facts):
        state["in_game_entry_passed"] = False


def vlm_login_verify_passed(
    state: LaunchGraphState,
    *,
    min_confidence: float = 0.85,
    facts: LaunchFacts | None = None,
) -> bool:
    """atomic_login VLM 验收：未指回登录且置信足够。"""
    if _vlm_indicates_login_screen(state, facts):
        return False
    judgment = state.get("last_game_entry_judgment")
    if not isinstance(judgment, dict):
        return False
    stage = str(judgment.get("stage") or "").strip().lower()
    if stage in _VLM_LOGIN_STAGES:
        return False
    conf = float(judgment.get("confidence") or 0)
    return conf >= min_confidence


def store_game_entry_judgment(state: LaunchGraphState, judgment) -> None:
    if judgment is None:
        return
    try:
        state["last_game_entry_judgment"] = judgment.model_dump()
    except AttributeError:
        if isinstance(judgment, dict):
            state["last_game_entry_judgment"] = judgment


def clear_game_entry_judgment(state: LaunchGraphState) -> None:
    state.pop("last_game_entry_judgment", None)


def reconcile_action_frames(state: LaunchGraphState, facts: LaunchFacts) -> LaunchFacts:
    """
    根据最近一次 ActionFrame wrong_route 检讨，修正 facts 使 DFS 回到正确里程碑。
  仅修补 facts，不改变路由图结构。
    """
    raw = state.get("last_reflection") or {}
    if not isinstance(raw, dict):
        return facts
    if str(raw.get("root_cause") or "") != "wrong_route":
        return facts

    patches = raw.get("fact_patches") or {}
    if not isinstance(patches, dict) or not patches:
        return facts

    merged = facts.model_copy(update=patches)
    state["facts"] = merged.model_dump()
    hint = str(raw.get("recover_hint") or "")[:200]
    if hint:
        state["recover_hint"] = hint
    state["last_reflection"] = {}
    return merged
