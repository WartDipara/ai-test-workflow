from __future__ import annotations

from pathlib import Path

from game_agent.graphs.launch_tree import _login_blocking
from game_agent.models.launch_graph_state import LaunchFacts, empty_launch_graph_state
from game_agent.services.login_secure_keyboard import (
    bump_blackout_streak,
    is_login_flow_in_progress,
    is_login_secure_keyboard_blackout,
    reset_blackout_streak,
    should_handle_secure_keyboard_blackout,
    should_press_back_for_blackout,
)
from game_agent.utils.ocr_util import OcrBbox


def _state(**kwargs):
    state = empty_launch_graph_state()
    state.update(kwargs)
    return state


def test_login_blocking_uses_state_flags_when_ocr_clear(tmp_path: Path) -> None:
    from PIL import Image

    black = tmp_path / "black.png"
    Image.new("RGB", (100, 100), (0, 0, 0)).save(black)
    facts = LaunchFacts(login_blocking=False, login_stage="clear")
    state = _state(account_filled=True, password_filled=True, login_submitted=False)
    assert _login_blocking(state, facts) is True
    assert is_login_secure_keyboard_blackout(black, [], ocr_summary="")


def test_login_flow_in_progress_from_password_filled() -> None:
    state = _state(password_filled=True, login_done=False)
    assert is_login_flow_in_progress(state) is True


def test_login_blackout_requires_black_image_and_empty_ocr(tmp_path: Path) -> None:
    from PIL import Image

    black = tmp_path / "black.png"
    normal = tmp_path / "normal.png"
    Image.new("RGB", (200, 200), (0, 0, 0)).save(black)
    Image.new("RGB", (200, 200), (200, 200, 200)).save(normal)
    bbox = OcrBbox(text="登录", cx=50, cy=50, x1=30, y1=40, x2=70, y2=60)
    assert is_login_secure_keyboard_blackout(black, [], ocr_summary="") is True
    assert is_login_secure_keyboard_blackout(normal, [], ocr_summary="") is False
    assert is_login_secure_keyboard_blackout(black, [bbox, bbox], ocr_summary="x") is False


def test_post_login_blackout_should_handle() -> None:
    state = _state(login_done=True, sub_account_selected=False)
    assert is_login_flow_in_progress(state) is False
    assert should_handle_secure_keyboard_blackout(state) is True


def test_post_login_blackout_streak_escalation() -> None:
    state = _state(login_done=True, sub_account_selected=False)
    assert bump_blackout_streak(state) == 1
    assert should_press_back_for_blackout(1) is False
    assert bump_blackout_streak(state) == 2
    assert should_press_back_for_blackout(2) is False
    assert bump_blackout_streak(state) == 3
    assert should_press_back_for_blackout(3) is True
    reset_blackout_streak(state)
    assert state.get("secure_keyboard_blackout_streak") == 0


def test_no_blackout_handling_after_sub_account_selected() -> None:
    state = _state(login_done=True, sub_account_selected=True)
    assert should_handle_secure_keyboard_blackout(state) is False
