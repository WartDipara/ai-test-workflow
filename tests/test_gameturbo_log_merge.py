from __future__ import annotations

from pathlib import Path

from game_agent.external_services.gameturbo.log import (
    ensure_gameturbo_log_for_analysis,
    gameturbo_log_path,
    merge_gameturbo_session_archives,
)


def _write(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_merge_gameturbo_session_archives_orders_and_dedups(tmp_path: Path) -> None:
    _write(
        tmp_path / "gameturbo_session_001.log",
        "06-11 16:28:03.526  1202 I GameTurbo: [SOCKET] fd=220",
        "06-11 16:28:05.972  1202 D GameTurbo: [BHOOK] OK",
    )
    _write(
        tmp_path / "gameturbo_session_002.log",
        "06-11 16:30:10.000  1202 D GameTurbo: E2E RTT: 50ms",
    )
    _write(
        gameturbo_log_path(tmp_path),
        "06-11 16:44:43.729  1202 D GameTurbo: E2E RTT: 65ms",
        "06-11 16:28:03.526  1202 I GameTurbo: [SOCKET] fd=220",
    )

    merged = merge_gameturbo_session_archives(tmp_path)
    lines = merged.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 4
    assert "[BHOOK]" in lines[1]
    assert lines[-1].endswith("E2E RTT: 65ms")
    assert lines.count("06-11 16:28:03.526  1202 I GameTurbo: [SOCKET] fd=220") == 1


def test_ensure_gameturbo_log_for_analysis_merges_sessions(tmp_path: Path) -> None:
    _write(
        tmp_path / "gameturbo_session_001.log",
        "06-11 16:28:03.526  1202 I GameTurbo: [SOCKET] fd=220",
    )
    gameturbo_log_path(tmp_path).write_text("", encoding="utf-8")

    path = ensure_gameturbo_log_for_analysis(tmp_path)

    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "[SOCKET] fd=220" in text
