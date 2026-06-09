import zipfile
from pathlib import Path

from game_agent.modules.preprocessing.preprocessor import Preprocessor


def _make_apk_with_abis(path: Path, abis: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for abi in abis:
            zf.writestr(f"lib/{abi}/libfoo.so", b"x")
        zf.writestr("classes.dex", b"dex")


def test_strip_abis_respects_preserved_list(tmp_path: Path) -> None:
    apk = tmp_path / "game.apk"
    _make_apk_with_abis(apk, ["arm64-v8a", "x86_64"])
    pre = Preprocessor(
        cache_dir=tmp_path,
        packages_dir=tmp_path / "pkg",
        preserved_abis=["arm64-v8a"],
    )
    out, removed, kept = pre._strip_abis(apk)
    assert out is not None
    assert removed == ["x86_64"]
    assert kept == ["arm64-v8a"]
