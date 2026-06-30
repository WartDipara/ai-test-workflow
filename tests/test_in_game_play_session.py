"""in-game play session：无 deadline 会话标志。"""

from __future__ import annotations

from game_agent.models.settings import GameSection
from game_agent.modules.run_context import AttemptContext


def test_in_game_brain_config_defaults() -> None:
    cfg = GameSection()
    assert cfg.in_game_success_confirm_rounds == 2
    assert cfg.in_game_fail_min_confidence == 0.75


def test_is_in_game_play_active_session_flag() -> None:
    ctx = AttemptContext()
    assert not ctx.is_in_game_play_active()
    ctx.signal_in_game_play_started()
    assert ctx.is_in_game_play_active()
    ctx.signal_in_game_confirmed("done")
    assert not ctx.is_in_game_play_active()


def test_clear_in_game_play_session() -> None:
    ctx = AttemptContext()
    ctx.signal_in_game_play_started()
    assert ctx.is_in_game_play_active()
    ctx.clear_in_game_play_session()
    assert not ctx.is_in_game_play_active()
