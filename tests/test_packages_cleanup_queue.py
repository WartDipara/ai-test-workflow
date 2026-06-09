from __future__ import annotations

from pathlib import Path

from game_agent.utils.packages_cleanup import (
    cleanup_deploy_artifacts,
    cleanup_task_packages,
)


def test_cleanup_task_packages_only_removes_gid_files(tmp_path: Path) -> None:
    gid_a = "111"
    gid_b = "222"
    source_a = tmp_path / f"{gid_a}_game.apk"
    source_b = tmp_path / f"{gid_b}_game.apk"
    deploy_a = tmp_path / f"{gid_a}_gameturbo.apk"
    deploy_b = tmp_path / f"{gid_b}_gameturbo.apk"
    for path in (source_a, source_b, deploy_a, deploy_b):
        path.write_bytes(b"PK")

    summary = cleanup_task_packages(gid_a, source_a, packages_dir=tmp_path)

    assert deploy_a.name in summary["deploy"]
    assert source_a.name in summary["source"]
    assert source_b.is_file()
    assert deploy_b.is_file()


def test_cleanup_deploy_artifacts_gid_prefix(tmp_path: Path) -> None:
    (tmp_path / "15993_gameturbo.apk").write_bytes(b"PK")
    (tmp_path / "15993_gameturbo.apk.idsig").write_bytes(b"sig")
    (tmp_path / "game_gameturbo.apk").write_bytes(b"PK")

    removed = cleanup_deploy_artifacts(tmp_path, gid="15993")
    assert "15993_gameturbo.apk" in removed
    assert "15993_gameturbo.apk.idsig" in removed
    assert (tmp_path / "game_gameturbo.apk").is_file()
