from pathlib import Path

from game_agent.modules.preprocessing.apk_resolver import (
    ApkSourceKind,
    resolve_apk_for_preprocess,
    resolve_failure_message,
)


def test_resolve_uses_cache_when_no_apks_txt(tmp_path: Path) -> None:
    apk = tmp_path / "game.apk"
    apk.write_bytes(b"PK")
    resolved = resolve_apk_for_preprocess(tmp_path)
    assert resolved is not None
    assert resolved.path == apk.resolve()
    assert resolved.source == ApkSourceKind.CACHE


def test_resolve_none_when_empty(tmp_path: Path) -> None:
    assert resolve_apk_for_preprocess(tmp_path) is None
    msg = resolve_failure_message(tmp_path)
    assert "apks.txt" in msg
