from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

from game_agent.controllers.foreground_coordinator import ForegroundCoordinator
from game_agent.models.settings import (
    AppConfig,
    ForegroundGuardSection,
    GameSection,
    GameTurboSection,
    LLMSection,
    ModulesSection,
)
from game_agent.models.task_config import TaskConfig
from game_agent.models.task_runtime import TaskRuntime
from game_agent.modules.run_context import AttemptContext, block_until_foreground_ready


def _task_config(tmp_path, *, max_recoveries: int = 3) -> TaskConfig:
    runtime = TaskRuntime(
        task_id="t1",
        index=0,
        serial="dev1",
        apk_url="http://example/a.apk",
        batch_root=tmp_path,
        task_cache_dir=tmp_path / "cache",
        package_name="com.game.app",
        launch_activity="com.game.app/.Main",
    )
    app_config = AppConfig(
        llm=LLMSection(base_url="http://x", api_key="k", model_name="gpt-4o"),
        game=GameSection(),
        gameturbo=GameTurboSection(),
        modules=ModulesSection(executor=False),
        foreground_guard=ForegroundGuardSection(
            enabled=True,
            poll_interval_s=2.0,
            max_recoveries=max_recoveries,
            recover_verify_delay_s=0.5,
        ),
    )
    return TaskConfig(app_config, runtime)


def test_attempt_context_wait_foreground_ready_unblocks() -> None:
    actx = AttemptContext()
    actx.set_foreground_lost(True)
    seen: list[bool] = []

    def waiter() -> None:
        ok = actx.wait_foreground_ready(timeout=2.0)
        seen.append(ok)

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    actx.set_foreground_lost(False)
    thread.join(timeout=2.0)
    assert seen == [True]
    assert actx.is_foreground_lost() is False


def test_block_until_foreground_ready_returns_false_on_fatal() -> None:
    actx = AttemptContext()
    actx.set_foreground_lost(True)
    actx.signal_fatal("foreground recover failed")
    assert block_until_foreground_ready(actx, poll_interval_s=0.1) is False


def test_foreground_coordinator_target_in_foreground(tmp_path) -> None:
    cfg = _task_config(tmp_path)
    actx = AttemptContext()
    adb = MagicMock()
    adb.current_foreground_app.return_value = ("com.game.app", "com.game.app/.Main")

    coord = ForegroundCoordinator(adb=adb, app_config=cfg, attempt_context=actx)
    with patch("game_agent.controllers.foreground_coordinator.time.sleep"):
        assert coord._poll_and_recover_once("com.game.app") is None
    assert actx.is_foreground_lost() is False


def test_foreground_coordinator_recovers_after_launch(tmp_path) -> None:
    cfg = _task_config(tmp_path)
    actx = AttemptContext()
    adb = MagicMock()
    adb.current_foreground_app.side_effect = [
        ("com.android.chrome", "com.android.chrome/.Main"),
        ("com.game.app", "com.game.app/.Main"),
    ]
    adb.launch_game.return_value = "Launched"

    coord = ForegroundCoordinator(adb=adb, app_config=cfg, attempt_context=actx)
    with patch("game_agent.controllers.foreground_coordinator.time.sleep"):
        assert coord._poll_and_recover_once("com.game.app") is None
    assert actx.is_foreground_lost() is False
    adb.launch_game.assert_called_once_with("com.game.app", "com.game.app/.Main")


def test_foreground_coordinator_fatal_after_max_recoveries(tmp_path) -> None:
    cfg = _task_config(tmp_path, max_recoveries=2)
    actx = AttemptContext()
    adb = MagicMock()
    adb.current_foreground_app.return_value = ("com.android.chrome", ".Main")
    adb.launch_game.return_value = "Launched"

    coord = ForegroundCoordinator(adb=adb, app_config=cfg, attempt_context=actx)
    with patch("game_agent.controllers.foreground_coordinator.time.sleep"):
        assert coord._poll_and_recover_once("com.game.app") is None
        msg = coord._poll_and_recover_once("com.game.app")
    assert msg is not None
    assert "前台应用丢失" in msg
    assert actx.should_stop_executor() is True


def test_foreground_coordinator_run_until_fatal_on_stop(tmp_path) -> None:
    cfg = _task_config(tmp_path)
    actx = AttemptContext()
    adb = MagicMock()
    adb.current_foreground_app.return_value = ("com.game.app", ".Main")
    stop = asyncio.Event()
    stop.set()

    coord = ForegroundCoordinator(adb=adb, app_config=cfg, attempt_context=actx)
    assert asyncio.run(coord.run_until_fatal(stop)) is None
