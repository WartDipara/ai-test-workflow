from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from game_agent.models.run_failure import classify_failure
from game_agent.external_services.gameturbo.log import (
    format_latest_gameturbo_log_for_agent,
    resolve_pipeline_artifact_root,
    tail_gameturbo_log_lines,
)
from game_agent.external_services.gameturbo.log_health import assess_gameturbo_log_health
from game_agent.controllers.network_anomaly_coordinator import (
    NetworkAnomalyCoordinator,
    format_confirmed_network_anomaly,
    format_confirmed_vision_ocr_anomaly,
)
from game_agent.external_services.gameturbo.config_retry import infer_blocked_stage
from game_agent.services.screen_download_health import (
    ScreenProgressTracker,
    is_download_stall_watch_stage,
    parse_download_percent_from_ocr,
    parse_percent_from_progress_text,
)


def test_log_health_fatal_marker() -> None:
    text = "06-09 12:00:00.000 1234 5678 I GameTurbo: tunnel closed\n"
    v = assess_gameturbo_log_health(text, min_lines=1)
    assert v.suspect
    assert "fatal" in v.markers


def test_log_health_pending_without_route() -> None:
    lines = ["line"] * 16
    lines.extend(
        f"[PENDING-SNI] 1.2.3.4:443 #{i}" for i in range(4)
    )
    v = assess_gameturbo_log_health("\n".join(lines))
    assert v.suspect
    assert "pending_no_route" in v.markers


def test_screen_progress_stall() -> None:
    tracker = ScreenProgressTracker()
    v1 = tracker.observe(
        stage="resource_download",
        progress="12%",
        percent=12,
        stall_s=90.0,
    )
    assert not v1.suspect
    tracker.last_change_monotonic = time.monotonic() - 95.0
    v2 = tracker.observe(
        stage="resource_download",
        progress="12%",
        percent=12,
        stall_s=90.0,
    )
    assert v2.suspect
    assert "stage=resource_download progress unchanged" in v2.reason


def test_unknown_stage_static_not_stall() -> None:
    tracker = ScreenProgressTracker()
    tracker.last_change_monotonic = time.monotonic() - 120.0
    v = tracker.observe(stage="unknown", progress="", percent=None, stall_s=90.0)
    assert not v.suspect


def test_login_stage_static_not_stall() -> None:
    tracker = ScreenProgressTracker()
    tracker.last_change_monotonic = time.monotonic() - 120.0
    v = tracker.observe(stage="login", progress="", percent=None, stall_s=90.0)
    assert not v.suspect
    assert is_download_stall_watch_stage("login") is False


def test_log_soft_rules_skipped_during_privacy_stage() -> None:
    lines = ["line"] * 20
    text = "\n".join(lines)
    assert assess_gameturbo_log_health(text, min_lines=15, ui_stage="privacy").suspect is False


def test_log_soft_rules_skipped_during_login_form_stage() -> None:
    lines = ["line"] * 20
    text = "\n".join(lines)
    assert assess_gameturbo_log_health(text, min_lines=15, ui_stage="login_form").suspect is False


def test_log_no_send_tunnel_requires_download_stage() -> None:
    lines = [
        "06-11 12:00:00.000 I GameTurbo: E2E RTT: 42ms",
        "06-11 12:00:00.100 I GameTurbo: [BHOOK] OK",
        *["line"] * 28,
        "[SNI-TUNNEL] example.com -1 -1 1",
    ]
    text = "\n".join(lines)
    assert not assess_gameturbo_log_health(text, min_lines=15, ui_stage="unknown").suspect
    assert not assess_gameturbo_log_health(text, min_lines=15, ui_stage="login").suspect
    v = assess_gameturbo_log_health(text, min_lines=15, ui_stage="resource_download")
    assert v.suspect
    assert "no_send_tunnel" in v.markers


def test_infer_blocked_stage_ignores_old_download_wording() -> None:
    reason = "Observer network anomaly confirmed: screen=stage=unknown progress unchanged for 92s (unknown)"
    assert infer_blocked_stage(reason=reason, ui_stage="login") == "login"
    assert infer_blocked_stage(reason=reason, ui_stage="") == "unknown"


def test_infer_blocked_stage_resource_download_from_ui_stage() -> None:
    reason = "stage=resource_download progress unchanged for 95s (12%)"
    assert infer_blocked_stage(reason=reason, ui_stage="resource_download") == "resource_download"


def test_infer_blocked_stage_traditional_chinese() -> None:
    assert infer_blocked_stage(reason="OCR 選服列表", ui_stage="") == "server_select"
    assert infer_blocked_stage(reason="登入密碼", ui_stage="") == "login"


def test_infer_blocked_stage_english_blob() -> None:
    assert infer_blocked_stage(reason="server select dialog", ui_stage="") == "server_select"


def test_observer_fatal_message_includes_ui_stage() -> None:
    msg = format_confirmed_network_anomaly(
        log_reason="log hit",
        screen_reason="screen hit",
        ui_stage="login",
    )
    assert "Observer network anomaly confirmed" in msg
    assert "ui_stage=login" in msg


def test_ocr_ignores_top_region_percent() -> None:
    ocr = "120,80 15MB/s 0.99\n600,1200 下载 45% 0.95"
    assert parse_download_percent_from_ocr(ocr, screen_h=2400, min_y_ratio=0.15) == 45


def test_confirmed_vision_ocr_anomaly_is_retryable() -> None:
    reason = format_confirmed_vision_ocr_anomaly(
        ocr_reason="network dialog on screen: 网络连接失败",
        vision_reason="connection timeout",
        ui_stage="resource_download",
    )
    f = classify_failure(reason)
    assert f.retryable
    assert f.code.value == "E2002"


def test_legacy_observer_message_still_retryable() -> None:
    reason = (
        "Observer network anomaly confirmed (log + screen): "
        "log=fatal | screen=stage=resource_download progress unchanged"
    )
    f = classify_failure(reason)
    assert f.retryable
    assert f.code.value == "E2002"


def test_parse_percent_from_progress_text() -> None:
    assert parse_percent_from_progress_text("45%") == 45


def test_network_anomaly_ocr_poll_only_frequent_for_download_stages(tmp_path: Path) -> None:
    cfg = SimpleNamespace(
        network_anomaly=SimpleNamespace(use_ocr_poll=True, poll_interval_s=5.0),
    )
    coord = NetworkAnomalyCoordinator(
        adb=MagicMock(),
        app_config=cfg,  # type: ignore[arg-type]
        artifact_root=tmp_path,
    )

    assert coord._should_run_ocr_poll("resource_download") is True
    assert coord._should_run_ocr_poll("loading") is True

    assert coord._should_run_ocr_poll("character_creation") is True
    assert coord._should_run_ocr_poll("character_creation") is False
    coord._last_passive_ocr_monotonic -= 31.0
    assert coord._should_run_ocr_poll("character_creation") is True

    coord._last_passive_ocr_monotonic -= 16.0
    assert coord._should_run_ocr_poll("unknown") is True
    assert coord._should_run_ocr_poll("unknown") is False


def test_tail_gameturbo_log_lines(tmp_path) -> None:
    retry = tmp_path / "retry_1_test"
    retry.mkdir()
    log_path = retry / "gameturbo.log"
    log_path.write_text("\n".join(f"line-{i}" for i in range(150)) + "\n", encoding="utf-8")
    executor_art = retry / "executor"
    executor_art.mkdir()
    lines, path = tail_gameturbo_log_lines(executor_art, None, limit=100, refresh_from_device=False)
    assert path == log_path
    assert len(lines) == 100
    assert lines[0] == "line-50"
    assert lines[-1] == "line-149"


def test_format_latest_gameturbo_log_for_agent(tmp_path) -> None:
    retry = tmp_path / "retry_1_test"
    retry.mkdir()
    (retry / "gameturbo.log").write_text("06-09 12:00:00.000 I GameTurbo: E2E RTT: 42ms\n", encoding="utf-8")
    text = format_latest_gameturbo_log_for_agent(
        retry / "executor",
        None,
        limit=100,
        refresh_from_device=False,
    )
    assert "E2E RTT" in text
    assert "Latest 1 GameTurbo" in text


def test_resolve_pipeline_artifact_root() -> None:
    from pathlib import Path

    assert resolve_pipeline_artifact_root(Path("/a/retry/executor")).name == "retry"
