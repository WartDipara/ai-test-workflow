"""in-game play session：deadline 延长与配置解析。"""

from __future__ import annotations

import time

from game_agent.models.settings import GameSection
from game_agent.modules.run_context import AttemptContext


def test_resolve_in_game_run_s_smoke_default() -> None:
    cfg = GameSection(in_game_mode="smoke", in_game_smoke_s=180.0)
    assert cfg.resolve_in_game_run_s() == 180.0


def test_resolve_in_game_run_s_soak_mode() -> None:
    cfg = GameSection(in_game_mode="soak", in_game_soak_s=1200.0)
    assert cfg.resolve_in_game_run_s() == 1200.0


def test_resolve_launch_timeout_s_fallback() -> None:
    cfg = GameSection(timeout_s=900.0)
    assert cfg.resolve_launch_timeout_s() == 900.0
    cfg2 = GameSection(timeout_s=900.0, launch_timeout_s=1200.0)
    assert cfg2.resolve_launch_timeout_s() == 1200.0


def test_extend_parallel_deadline_after_play_started() -> None:
    ctx = AttemptContext()
    play_deadline = time.monotonic() + 180.0
    ctx.signal_in_game_play_started(play_deadline, buffer_s=60.0)
    base = time.monotonic() + 300.0
    extended = ctx.extend_parallel_deadline(base)
    assert extended >= play_deadline + 60.0 - 0.1


def test_is_in_game_play_active_before_deadline() -> None:
    ctx = AttemptContext()
    ctx.signal_in_game_play_started(time.monotonic() + 120.0, buffer_s=30.0)
    assert ctx.is_in_game_play_active()
