from __future__ import annotations

from game_agent.external_services.gameturbo.bootstrap import (
    artifact_merged_config_path,
    find_merged_config_for_deliverable,
    merged_config_path,
    needs_gameturbo_deploy,
    output_apk_name,
    output_apk_path,
    resolve_merged_config_deploy_path,
)
from game_agent.external_services.gameturbo.paths import gameturbo_merged_config_path


def test_output_apk_name_per_gid() -> None:
    assert output_apk_name("15993") == "15993_gameturbo.apk"
    assert output_apk_name() == "game_gameturbo.apk"


def test_merged_config_path_per_gid() -> None:
    assert merged_config_path("15993").name == ".gameturbo_merged_15993.json"
    assert gameturbo_merged_config_path("15993").name == ".gameturbo_merged_15993.json"


def test_resolve_merged_config_deploy_path_prefers_artifact(tmp_path) -> None:
    artifact = tmp_path / "retry_1"
    path = resolve_merged_config_deploy_path(
        "7734",
        artifact_root=artifact,
    )
    assert path == artifact.resolve() / ".gameturbo_merged_7734.json"


def test_find_merged_config_for_deliverable(tmp_path) -> None:
    artifact = tmp_path / "retry_1"
    artifact.mkdir()
    merged = artifact_merged_config_path(artifact, "7734")
    merged.write_text("{}", encoding="utf-8")
    found = find_merged_config_for_deliverable("7734", winning_artifact_root=artifact)
    assert found == merged.resolve()


def test_needs_gameturbo_deploy_when_apk_exists_but_not_installed(tmp_path) -> None:
    apk = tmp_path / "7734_gameturbo.apk"
    config = tmp_path / "gameturbo_7734_test.json"
    config.write_text("{}", encoding="utf-8")
    apk.write_bytes(b"x")
    import os
    import time

    os.utime(apk, (time.time() - 120, time.time() - 120))
    assert needs_gameturbo_deploy(apk, package_installed=False, game_config_path=config) is True
    assert needs_gameturbo_deploy(apk, package_installed=True, game_config_path=config) is False


def test_needs_gameturbo_deploy_skips_when_apk_newer_than_config(tmp_path) -> None:
    apk = tmp_path / "7734_gameturbo.apk"
    config = tmp_path / "gameturbo_7734_test.json"
    config.write_text("{}", encoding="utf-8")
    apk.write_bytes(b"x")
    import os
    import time

    old = time.time() - 60
    os.utime(config, (old, old))
    os.utime(apk, (time.time(), time.time()))
    assert needs_gameturbo_deploy(apk, package_installed=False, game_config_path=config) is False


def test_needs_gameturbo_deploy_when_apk_missing(tmp_path) -> None:
    apk = tmp_path / "missing.apk"
    assert needs_gameturbo_deploy(apk, package_installed=True) is False
    assert needs_gameturbo_deploy(apk, package_installed=False) is True


def test_output_apk_path_uses_gid(tmp_path) -> None:
    from game_agent.external_services.gameturbo import bootstrap as gb

    original = gb.PACKAGES_DIR
    gb.PACKAGES_DIR = tmp_path
    try:
        assert output_apk_path("42").name == "42_gameturbo.apk"
    finally:
        gb.PACKAGES_DIR = original
