"""Deprecated shim — import from core or external_services.gameturbo instead."""

from __future__ import annotations

from game_agent.core.apk_staging import parse_gid_from_apk_name, resolve_task_gid
from game_agent.external_services.gameturbo.bootstrap import (
    GameTurboBootstrapResult,
    artifact_merged_config_path,
    discover_source_apk,
    discover_source_apk_for_gid,
    finalize_merged_config_after_deploy,
    find_merged_config_for_deliverable,
    init_game_config_from_template,
    merged_config_path,
    needs_gameturbo_deploy,
    needs_initial_preprocess,
    output_apk_name,
    output_apk_path,
    peek_gid_from_packages,
    resolve_existing_game_config,
    resolve_game_config,
    resolve_merged_config_deploy_path,
    run_bootstrap_from_source,
)
from game_agent.external_services.gameturbo.paths import (
    GAMES_DIR,
    GAMETURBO_MERGED_CONFIG_PATH,
    OUTPUT_APK_NAME,
    PACKAGES_DIR,
    gameturbo_merged_config_path,
)

__all__ = [
    "GAMES_DIR",
    "GAMETURBO_MERGED_CONFIG_PATH",
    "GameTurboBootstrapResult",
    "OUTPUT_APK_NAME",
    "PACKAGES_DIR",
    "artifact_merged_config_path",
    "discover_source_apk",
    "discover_source_apk_for_gid",
    "finalize_merged_config_after_deploy",
    "find_merged_config_for_deliverable",
    "gameturbo_merged_config_path",
    "init_game_config_from_template",
    "merged_config_path",
    "needs_gameturbo_deploy",
    "needs_initial_preprocess",
    "output_apk_name",
    "output_apk_path",
    "parse_gid_from_apk_name",
    "peek_gid_from_packages",
    "resolve_existing_game_config",
    "resolve_game_config",
    "resolve_merged_config_deploy_path",
    "resolve_task_gid",
    "run_bootstrap_from_source",
]
