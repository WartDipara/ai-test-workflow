from __future__ import annotations

import time

from game_agent.models.run_failure import classify_failure
from game_agent.services.gameturbo_log import (
    format_latest_gameturbo_log_for_agent,
    resolve_pipeline_artifact_root,
    tail_gameturbo_log_lines,
)
from game_agent.services.gameturbo_log_health import assess_gameturbo_log_health
from game_agent.services.screen_download_health import (
    ScreenProgressTracker,
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


def test_ocr_ignores_top_region_percent() -> None:
    ocr = "120,80 15MB/s 0.99\n600,1200 下载 45% 0.95"
    assert parse_download_percent_from_ocr(ocr, screen_h=2400, min_y_ratio=0.15) == 45


def test_confirmed_anomaly_is_retryable() -> None:
    reason = (
        "Network anomaly confirmed (log + screen): "
        "log=fatal | screen=download stuck"
    )
    f = classify_failure(reason)
    assert f.retryable
    assert f.code.value == "E2001"


def test_parse_percent_from_progress_text() -> None:
    assert parse_percent_from_progress_text("45%") == 45


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
