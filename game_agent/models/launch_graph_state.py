"""LangGraph 进入游戏流程状态模型。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

LaunchRouteTarget = Literal[
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
    "end",
]

class LaunchNodeStatus(BaseModel):
    """单个图节点执行记录。"""

    node: str
    done: bool = False
    failed: bool = False
    attempts: int = 0
    last_error: str = ""
    last_artifact: str = ""
    evidence: str = ""


class LaunchFacts(BaseModel):
    """当前屏幕结构化事实（由 classify_screen 写入）。"""

    login_blocking: bool = False
    login_stage: str = "clear"
    sub_account_blocking: bool = False
    sub_account_action_xy: tuple[int, int] | None = None
    sub_account_label: str = ""

    initial_privacy_dialog: bool = False
    agree_button_xy: tuple[int, int] | None = None

    terms_checkbox_visible: bool = False
    enter_cta_visible: bool = False
    enter_cta_xy: tuple[int, int] | None = None
    enter_cta_label: str = ""

    server_slot_visible: bool = False
    download_visible: bool = False
    announcement_overlay: bool = False
    announcement_dismiss_xy: tuple[int, int] | None = None

    character_creation_blocking: bool = False

    vision_stage: str = ""
    vision_has_anomaly: bool = False
    vision_anomaly_reason: str = ""

    interpreter_stage: str = ""
    screen_completion_signals: list[str] = Field(default_factory=list)
    interpreter_reason: str = ""

    classify_reason: str = ""


class LaunchGraphState(TypedDict, total=False):
    """LangGraph 共享状态（可序列化字段）。"""

    current_stage: str
    facts: dict[str, Any]
    completed_nodes: dict[str, dict[str, Any]]
    failed_nodes: dict[str, dict[str, Any]]
    last_screenshot: str
    last_ocr_summary: str
    last_bboxes: list[dict[str, int | str]]
    interpret_screenshot_hash: str
    last_analyze_screen_ts: float
    last_vision_summary: str
    gameturbo_summary: str
    privacy_checked: bool
    account_filled: bool
    password_filled: bool
    login_submitted: bool
    login_done: bool
    sub_account_selected: bool
    server_checked: bool
    enter_tapped_count: int
    in_game_entry_passed: bool
    in_game_confirmed: bool
    recover_hint: str
    terminal_error: str
    finished: bool
    iteration: int
    last_route: str
    planned_next_route: str
    last_node: str
    pending_vision_path: str
    vision_enrichment_status: str
    tree_trace: str
    current_tree_node: str
    free_rounds: int
    free_no_progress_rounds: int
    free_last_action_signature: str
    free_last_progress_fingerprint: str
    free_same_action_streak: int
    dynamic_chain: list[dict[str, Any]]
    dynamic_cursor: int
    dynamic_rounds: int
    dynamic_no_progress: int
    dynamic_last_fingerprint: str
    dynamic_failed: bool
    stability_observe_started_at: float
    stability_observe_deadline: float
    stability_last_check_at: float
    stability_rounds: int
    free_in_game_ocr_hits: list[str]
    adaptive_flow_done: bool
    adaptive_rounds: int
    adaptive_no_progress: int
    adaptive_phase_tree: list[dict[str, Any]]
    adaptive_active_node_id: str
    current_phase_spec: dict[str, Any]
    phase_registry: list[dict[str, Any]]
    phase_entry_fingerprint: str
    phase_last_fingerprint: str
    phase_replan_count: int
    launch_graph_limits: dict[str, Any]


def empty_launch_graph_state() -> LaunchGraphState:
    return LaunchGraphState(
        current_stage="launch",
        facts={},
        completed_nodes={},
        failed_nodes={},
        last_screenshot="",
        last_ocr_summary="",
        last_bboxes=[],
        interpret_screenshot_hash="",
        last_analyze_screen_ts=0.0,
        last_vision_summary="",
        gameturbo_summary="",
        privacy_checked=False,
        account_filled=False,
        password_filled=False,
        login_submitted=False,
        login_done=False,
        sub_account_selected=False,
        server_checked=False,
        enter_tapped_count=0,
        in_game_entry_passed=False,
        in_game_confirmed=False,
        recover_hint="",
        terminal_error="",
        finished=False,
        iteration=0,
        last_route="",
        planned_next_route="",
        last_node="",
        pending_vision_path="",
        vision_enrichment_status="",
        tree_trace="",
        current_tree_node="",
        free_rounds=0,
        free_no_progress_rounds=0,
        free_last_action_signature="",
        free_last_progress_fingerprint="",
        free_same_action_streak=0,
        dynamic_chain=[],
        dynamic_cursor=0,
        dynamic_rounds=0,
        dynamic_no_progress=0,
        dynamic_last_fingerprint="",
        dynamic_failed=False,
        stability_observe_started_at=0.0,
        stability_observe_deadline=0.0,
        stability_last_check_at=0.0,
        stability_rounds=0,
        free_in_game_ocr_hits=[],
        adaptive_flow_done=False,
        adaptive_rounds=0,
        adaptive_no_progress=0,
        adaptive_phase_tree=[],
        adaptive_active_node_id="",
        current_phase_spec={},
        phase_registry=[],
        phase_entry_fingerprint="",
        phase_last_fingerprint="",
        phase_replan_count=0,
        launch_graph_limits={},
    )


def facts_from_state(state: LaunchGraphState) -> LaunchFacts:
    raw = state.get("facts") or {}
    return LaunchFacts.model_validate(raw)


def node_status_from_dict(data: dict[str, Any] | None) -> LaunchNodeStatus:
    if not data:
        return LaunchNodeStatus(node="")
    return LaunchNodeStatus.model_validate(data)


def mark_node_done(state: LaunchGraphState, node: str, *, artifact: str = "", evidence: str = "") -> None:
    completed = dict(state.get("completed_nodes") or {})
    prev = node_status_from_dict(completed.get(node))
    completed[node] = LaunchNodeStatus(
        node=node,
        done=True,
        failed=False,
        attempts=prev.attempts + 1,
        last_artifact=artifact,
        evidence=evidence[:500],
    ).model_dump()
    state["completed_nodes"] = completed
    failed = dict(state.get("failed_nodes") or {})
    failed.pop(node, None)
    state["failed_nodes"] = failed
    state["last_node"] = node


def mark_node_failed(
    state: LaunchGraphState,
    node: str,
    error: str,
    *,
    artifact: str = "",
    evidence: str = "",
) -> None:
    failed = dict(state.get("failed_nodes") or {})
    completed = dict(state.get("completed_nodes") or {})
    prev = node_status_from_dict(failed.get(node) or completed.get(node))
    status = LaunchNodeStatus(
        node=node,
        done=False,
        failed=True,
        attempts=prev.attempts + 1,
        last_error=error[:500],
        last_artifact=artifact,
        evidence=evidence[:500],
    )
    failed[node] = status.model_dump()
    state["failed_nodes"] = failed
    state["last_node"] = node


def node_attempts(state: LaunchGraphState, node: str) -> int:
    for bucket in (state.get("failed_nodes") or {}, state.get("completed_nodes") or {}):
        if node in bucket:
            return int(bucket[node].get("attempts", 0))
    return 0
