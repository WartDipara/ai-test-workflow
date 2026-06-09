from __future__ import annotations

from pathlib import Path

from game_agent.controllers.batch_urls import resolve_batch_urls


def test_resolve_batch_urls_from_apks_txt(tmp_path: Path) -> None:
    (tmp_path / "apks.txt").write_text(
        "https://cdn.example.com/a.apk\nhttps://cdn.example.com/b.apk\n",
        encoding="utf-8",
    )
    assert len(resolve_batch_urls(tmp_path)) == 2


def test_resolve_batch_urls_single_from_cache(tmp_path: Path) -> None:
    (tmp_path / "game.apk").write_bytes(b"PK")
    urls = resolve_batch_urls(tmp_path)
    assert urls == [""]


def test_resolve_batch_urls_empty(tmp_path: Path) -> None:
    assert resolve_batch_urls(tmp_path) == []
