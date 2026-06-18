from __future__ import annotations

from game_agent.graphs.launch_routing import route_next
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state


def _state(**kwargs):
    state = empty_launch_graph_state()
    facts = kwargs.pop("facts", LaunchFacts())
    state["facts"] = facts.model_dump()
    state.update(kwargs)
    return state


def test_route_end_when_in_game() -> None:
    state = _state(in_game_confirmed=True)
    assert route_next(state) == "end"


def test_route_atomic_login_before_enter() -> None:
    state = _state(
        facts=LaunchFacts(login_blocking=True, login_stage="login_form"),
        login_done=False,
    )
    assert route_next(state) == "atomic_login"


def test_route_atomic_login_when_ocr_clear_but_not_done() -> None:
    """OCR 空（黑屏）时仍凭状态位走 atomic_login。"""
    state = _state(
        facts=LaunchFacts(login_blocking=False, login_stage="clear"),
        account_filled=True,
        password_filled=False,
        login_done=False,
    )
    assert route_next(state) == "atomic_login"


def test_route_no_login_when_done() -> None:
    state = _state(
        facts=LaunchFacts(login_blocking=True, login_stage="login_form"),
        login_done=True,
    )
    assert route_next(state) != "atomic_login"


def test_route_sub_account_before_server() -> None:
    state = _state(
        facts=LaunchFacts(
            sub_account_blocking=True,
            login_stage="sub_account_select",
            enter_cta_visible=True,
            enter_cta_xy=(500, 900),
        ),
        login_done=True,
    )
    assert route_next(state) == "select_sub_account"


def test_route_initial_privacy_modal_before_checkbox() -> None:
    state = _state(
        facts=LaunchFacts(
            initial_privacy_dialog=True,
            agree_button_xy=(780, 1285),
            terms_checkbox_visible=True,
            enter_cta_visible=True,
            enter_cta_xy=(400, 800),
        ),
        privacy_checked=False,
    )
    assert route_next(state) == "handle_initial_privacy_dialog"


def test_route_checkbox_before_enter() -> None:
    state = _state(
        facts=LaunchFacts(
            terms_checkbox_visible=True,
            enter_cta_visible=True,
            enter_cta_xy=(400, 800),
        ),
        privacy_checked=False,
    )
    assert route_next(state) == "ensure_privacy_checkbox"


def test_route_checkbox_before_login_when_both_visible() -> None:
    state = _state(
        facts=LaunchFacts(
            terms_checkbox_visible=True,
            login_blocking=True,
            login_stage="login_form",
            enter_cta_visible=True,
            enter_cta_xy=(400, 800),
        ),
        privacy_checked=False,
        login_done=False,
    )
    assert route_next(state) == "ensure_privacy_checkbox"


def test_route_server_after_login() -> None:
    state = _state(
        facts=LaunchFacts(
            server_slot_visible=True,
            enter_cta_visible=True,
            enter_cta_xy=(400, 800),
        ),
        login_done=True,
        server_checked=False,
    )
    assert route_next(state) == "check_server_selector"


def test_route_tap_enter_when_ready() -> None:
    state = _state(
        facts=LaunchFacts(
            enter_cta_visible=True,
            enter_cta_xy=(400, 800),
        ),
        privacy_checked=True,
        login_done=True,
        server_checked=True,
    )
    assert route_next(state) == "tap_enter_game"


def test_route_check_in_game_after_enter_tap() -> None:
    state = _state(
        facts=LaunchFacts(enter_cta_visible=False),
        privacy_checked=True,
        login_done=True,
        server_checked=True,
        enter_tapped_count=1,
    )
    assert route_next(state) == "check_in_game"


def test_route_second_enter_when_cta_still_visible() -> None:
    state = _state(
        facts=LaunchFacts(
            enter_cta_visible=True,
            enter_cta_xy=(400, 800),
        ),
        privacy_checked=True,
        login_done=True,
        server_checked=True,
        enter_tapped_count=1,
    )
    assert route_next(state) == "tap_enter_game"
