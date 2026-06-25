from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from game_agent.controllers.log_monitor_controller import LogMonitor
from game_agent.external_services.gameturbo.log import GAMETURBO_LOG_COLLECTOR
from game_agent.models.settings import AppConfig, GameSection, LLMSection


def _app_config() -> AppConfig:
    return AppConfig(
        llm=LLMSection(
            base_url="https://api.example.com",
            api_key="k",
            model_name="gpt-4o",
        ),
        llm_multimodal=LLMSection(
            base_url="https://api.example.com",
            api_key="k",
            model_name="gpt-4o",
        ),
        game=GameSection(timeout_s=300.0),
    )


def test_log_monitor_does_not_fatal_on_tunnel_closed_line(tmp_path: Path) -> None:
    async def _run() -> str | None:
        adb = MagicMock()
        cfg = _app_config()
        log_path = tmp_path / "gameturbo.log"
        log_path.write_text("", encoding="utf-8")

        class FakeProcess:
            returncode = None

            async def wait(self):
                return 0

            def terminate(self):
                self.returncode = 0

            def kill(self):
                self.returncode = 1

        proc = FakeProcess()
        read_count = 0

        async def fake_readline():
            nonlocal read_count
            read_count += 1
            if read_count == 1:
                return b"06-09 12:00:00.000 I GameTurbo: tunnel closed\n"
            stop.set()
            return b""

        proc.stdout = MagicMock()
        proc.stdout.readline = fake_readline

        async def fake_exec(*_a, **_k):
            return proc

        stop = asyncio.Event()
        monitor = LogMonitor(adb, cfg, tmp_path, GAMETURBO_LOG_COLLECTOR)

        with patch("asyncio.create_subprocess_exec", fake_exec):
            return await monitor._run_one_stream(stop, log_path=log_path, seen_keys=set())

    result = asyncio.run(_run())
    assert result is None
    content = (tmp_path / "gameturbo.log").read_text(encoding="utf-8")
    assert "tunnel closed" in content
