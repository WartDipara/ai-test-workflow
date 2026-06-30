"""GameTurbo-specific orchestration helpers (invoked via ExternalServiceManager)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from game_agent.core.apk_staging import parse_gid_from_apk_name
from game_agent.models.task_runtime import TaskRuntimeRegistry

if TYPE_CHECKING:
    from game_agent.models.task_runtime import TaskRuntime
    from game_agent.modules.preprocessing.preprocessor import PreprocessResult


def apply_preprocess_context(*, runtime: TaskRuntime, processed_apk: Path) -> None:
    from game_agent.external_services.gameturbo.bootstrap import resolve_game_config

    source_apk = processed_apk.resolve()
    gid = parse_gid_from_apk_name(source_apk)
    game_config_path, _created = resolve_game_config(gid)
    runtime.update_gameturbo(
        gid=gid,
        source_apk=source_apk,
        game_config_path=game_config_path,
    )
    TaskRuntimeRegistry.register(runtime)


def sync_runtime_from_packages(*, runtime: TaskRuntime, deploy_gid: str | None) -> None:
    from game_agent.external_services.gameturbo.bootstrap import (
        discover_source_apk,
        output_apk_path,
    )

    out_apk = output_apk_path(deploy_gid)
    apk: Path | None = out_apk if out_apk.is_file() else None
    if apk is None:
        try:
            apk = discover_source_apk(
                gid=deploy_gid,
                source_apk=runtime.source_apk,
            )
        except RuntimeError:
            return
    runtime.update_from_apk(apk)
    TaskRuntimeRegistry.register(runtime)


def preprocessing_packages_dir() -> Path:
    from game_agent.external_services.gameturbo.paths import PACKAGES_DIR

    return PACKAGES_DIR


def infer_blocked_stage(
    *,
    reason: str,
    ui_stage: str,
    ui_progress: str,
) -> str:
    from game_agent.external_services.gameturbo.config_retry import infer_blocked_stage as _infer

    return _infer(
        reason=reason,
        ui_stage=ui_stage,
        ui_progress=ui_progress,
    )


def format_executor_retry_brief(deliverable_root: Path) -> str:
    from game_agent.external_services.gameturbo.config_retry import (
        format_last_patch_for_executor,
    )

    return format_last_patch_for_executor(deliverable_root)


def resolve_orchestrator_source_apk(
    *,
    runtime: TaskRuntime,
    deploy_gid: str | None,
    preprocess_record: PreprocessResult | None,
) -> Path | None:
    if runtime.source_apk is not None and runtime.source_apk.is_file():
        return runtime.source_apk.resolve()
    if (
        preprocess_record is not None
        and preprocess_record.processed_apk is not None
        and preprocess_record.processed_apk.is_file()
    ):
        return preprocess_record.processed_apk.resolve()
    from game_agent.external_services.gameturbo.bootstrap import discover_source_apk

    try:
        return discover_source_apk(
            gid=deploy_gid,
            source_apk=runtime.source_apk,
        )
    except RuntimeError:
        return None


def require_success_merged_config(
    *,
    deploy_gid: str | None,
    winning_artifact_root: Path,
) -> Path:
    from game_agent.external_services.gameturbo.bootstrap import (
        find_merged_config_for_deliverable,
        merged_config_path,
    )
    from game_agent.external_services.gameturbo.paths import GAMETURBO_MERGED_CONFIG_PATH

    config_path = find_merged_config_for_deliverable(
        deploy_gid or "",
        winning_artifact_root=winning_artifact_root,
    )
    if config_path is not None:
        return config_path
    fallback = (
        merged_config_path(deploy_gid)
        if deploy_gid
        else GAMETURBO_MERGED_CONFIG_PATH
    )
    raise RuntimeError(
        f"Tests passed but deploy merge config missing (checked {winning_artifact_root} "
        f"and {fallback}); confirm deploy.sh ran and produced merge config"
    )


def cleanup_deploy_artifacts_for_gid(gid: str | None) -> list[str]:
    from game_agent.utils.packages_cleanup import cleanup_deploy_artifacts

    return cleanup_deploy_artifacts(gid=gid)


def finalize_task_packages(
    *,
    gid: str,
    source_apk: Path | None,
) -> dict[str, list[str]]:
    from game_agent.utils.packages_cleanup import cleanup_task_packages

    return cleanup_task_packages(gid, source_apk)
