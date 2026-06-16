"""变种流程：checkbox -> start -> login -> sub_account -> server -> enter -> in_game。"""

from __future__ import annotations

from game_agent.graphs.launch_routing import route_next
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state


def _apply_route(state, expected: str) -> str:
    target = route_next(state)
    assert target == expected, f"expected {expected}, got {target}"
    return target


def test_variant_checkbox_then_start_opens_login() -> None:
    """阶段1：有 checkbox + 开始游戏，应先勾协议再点进入。"""
    state = empty_launch_graph_state()
    state["facts"] = LaunchFacts(
        terms_checkbox_visible=True,
        enter_cta_visible=True,
        enter_cta_xy=(300, 850),
        enter_cta_label="开始游戏",
    ).model_dump()
    _apply_route(state, "ensure_privacy_checkbox")
    state["privacy_checked"] = True
    _apply_route(state, "tap_enter_game")
    state["enter_tapped_count"] = 1

    """阶段2：点开始游戏后出现登录窗。"""
    state["facts"] = LaunchFacts(
        login_blocking=True,
        login_stage="login_form",
        enter_cta_visible=True,
        enter_cta_xy=(300, 850),
    ).model_dump()
    _apply_route(state, "atomic_login")
    state["account_filled"] = True
    state["password_filled"] = True
    state["login_submitted"] = True
    state["login_done"] = True

    """阶段3：登录后出现小号选择。"""
    state["facts"] = LaunchFacts(
        sub_account_blocking=True,
        login_stage="sub_account_select",
        sub_account_action_xy=(1800, 600),
        enter_cta_visible=True,
    ).model_dump()
    _apply_route(state, "select_sub_account")
    state["sub_account_selected"] = True

    """阶段4：选服检查。"""
    state["facts"] = LaunchFacts(
        server_slot_visible=True,
        enter_cta_visible=True,
        enter_cta_xy=(300, 850),
    ).model_dump()
    _apply_route(state, "check_server_selector")
    state["server_checked"] = True

    """阶段5：再次点开始游戏并进游戏确认。"""
    _apply_route(state, "tap_enter_game")
    state["enter_tapped_count"] = 2
    state["facts"] = LaunchFacts(enter_cta_visible=False).model_dump()
    _apply_route(state, "check_in_game")
    state["in_game_confirmed"] = True
    assert route_next(state) == "end"
