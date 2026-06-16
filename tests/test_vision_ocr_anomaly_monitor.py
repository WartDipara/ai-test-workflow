from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from game_agent.controllers.network_anomaly_coordinator import NetworkAnomalyCoordinator
from game_agent.models.settings import AppConfig, GameSection, LLMSection, NetworkAnomalySection


def _cfg(*, require_multimodal_confirm: bool = True) -> AppConfig:
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
        network_anomaly=NetworkAnomalySection(
            enabled=True,
            require_multimodal_confirm=require_multimodal_confirm,
            poll_interval_s=2.0,
        ),
    )


def test_ocr_suspect_requires_multimodal_when_configured(tmp_path: Path) -> None:
    async def _run() -> str | None:
        adb = MagicMock()
        adb.touch_size.return_value = (1080, 2400)
        coord = NetworkAnomalyCoordinator(
            adb=adb,
            app_config=_cfg(require_multimodal_confirm=True),
            artifact_root=tmp_path,
        )
        tracker = MagicMock()
        tracker.observe.return_value = MagicMock(suspect=False, reason="")

        with (
            patch(
                "game_agent.controllers.network_anomaly_coordinator.NetworkAnomalyCoordinator._capture_ocr_summary",
                return_value=("ocr", "network error", str(tmp_path / "s.png")),
            ),
            patch("game_agent.workers.vision_worker.VisionWorker") as mock_vw,
        ):
            mock_vw.return_value.analyze_game_state = AsyncMock(
                return_value='{"has_anomaly": false, "stage": "unknown", "anomaly_reason": ""}',
            )
            return await coord._poll_once(tracker)

    assert asyncio.run(_run()) is None
    assert not (tmp_path / "anomaly_evidence.json").exists()


def test_ocr_plus_vision_confirms_fatal(tmp_path: Path) -> None:
    async def _run() -> str | None:
        adb = MagicMock()
        adb.touch_size.return_value = (1080, 2400)
        coord = NetworkAnomalyCoordinator(
            adb=adb,
            app_config=_cfg(require_multimodal_confirm=True),
            artifact_root=tmp_path,
        )
        tracker = MagicMock()
        tracker.observe.return_value = MagicMock(suspect=False, reason="")

        with (
            patch(
                "game_agent.controllers.network_anomaly_coordinator.NetworkAnomalyCoordinator._capture_ocr_summary",
                return_value=("ocr", "network error", str(tmp_path / "s.png")),
            ),
            patch("game_agent.workers.vision_worker.VisionWorker") as mock_vw,
        ):
            mock_vw.return_value.analyze_game_state = AsyncMock(
                return_value='{"has_anomaly": true, "stage": "resource_download", "anomaly_reason": "timeout"}',
            )
            return await coord._poll_once(tracker)

    msg = asyncio.run(_run())
    assert msg is not None
    assert "Vision/OCR network anomaly confirmed" in msg
    assert (tmp_path / "anomaly_evidence.json").is_file()
