"""GameTurbo bootstrap / deploy artifact helpers (plugin-only)."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from game_agent.core.apk_staging import parse_gid_from_apk_name
from game_agent.external_services.gameturbo.paths import (
    GAMETURBO_MERGED_CONFIG_PATH,
    GAMES_DIR,
    OUTPUT_APK_NAME,
    PACKAGES_DIR,
    gameturbo_merged_config_path,
)

logger = logging.getLogger(__name__)


def output_apk_name(gid: str | None = None) -> str:
    gid = (gid or "").strip()
    if gid:
        return f"{gid}_gameturbo.apk"
    return OUTPUT_APK_NAME


def merged_config_path(gid: str | None = None) -> Path:
    gid = (gid or "").strip()
    if gid:
        return gameturbo_merged_config_path(gid)
    return GAMETURBO_MERGED_CONFIG_PATH


def artifact_merged_config_path(artifact_root: Path, gid: str) -> Path:
    safe_gid = (gid or "").strip() or "unknown"
    return artifact_root.resolve() / f".gameturbo_merged_{safe_gid}.json"


def resolve_merged_config_deploy_path(
    gid: str,
    *,
    artifact_root: Path | None = None,
    merged_config_output: Path | None = None,
) -> Path | None:
    gid = (gid or "").strip()
    filename = (
        merged_config_output.name
        if merged_config_output is not None
        else (f".gameturbo_merged_{gid}.json" if gid else ".gameturbo_merged.json")
    )
    if artifact_root is not None:
        artifact_root.mkdir(parents=True, exist_ok=True)
        return artifact_root.resolve() / filename
    if gid:
        return gameturbo_merged_config_path(gid)
    return merged_config_output or GAMETURBO_MERGED_CONFIG_PATH


def finalize_merged_config_after_deploy(gid: str, target: Path) -> Path:
    gid = (gid or "").strip()
    target = target.resolve()
    native = gameturbo_merged_config_path(gid) if gid else GAMETURBO_MERGED_CONFIG_PATH

    if target.is_file():
        if native.is_file() and native.resolve() != target.resolve():
            try:
                native.unlink()
                logger.info("Removed GameTurbo-Native merge config leftover: %s", native)
            except OSError as exc:
                logger.warning("Failed to remove merge config leftover %s: %s", native, exc)
        return target

    if native.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(native), str(target))
        logger.info("Moved merge config to deliverables: %s", target)
        return target

    raise RuntimeError(
        f"deploy did not produce merge config (expected {target} or {native})",
    )


def find_merged_config_for_deliverable(
    gid: str,
    *,
    winning_artifact_root: Path | None = None,
) -> Path | None:
    gid = (gid or "").strip()
    candidates: list[Path] = []
    if winning_artifact_root is not None and gid:
        candidates.append(artifact_merged_config_path(winning_artifact_root, gid))
    if gid:
        candidates.append(gameturbo_merged_config_path(gid))
    candidates.append(GAMETURBO_MERGED_CONFIG_PATH)
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return None


@dataclass(frozen=True, slots=True)
class GameTurboBootstrapResult:
    gid: str
    source_apk: Path
    game_config_path: Path
    created_config: bool


def output_apk_path(gid: str | None = None) -> Path:
    return PACKAGES_DIR / output_apk_name(gid)


def needs_initial_preprocess(gid: str | None = None) -> bool:
    return not output_apk_path(gid).is_file()


def needs_gameturbo_deploy(
    output_apk: Path,
    *,
    package_installed: bool,
    game_config_path: Path | None = None,
) -> bool:
    if package_installed:
        return False
    if not output_apk.is_file():
        return True
    if game_config_path is not None and game_config_path.is_file():
        try:
            if output_apk.stat().st_mtime >= game_config_path.stat().st_mtime:
                return False
        except OSError:
            pass
    return True


def peek_gid_from_packages(packages_dir: Path = PACKAGES_DIR) -> str | None:
    try:
        return parse_gid_from_apk_name(discover_source_apk(packages_dir))
    except RuntimeError:
        return None


def discover_source_apk(
    packages_dir: Path = PACKAGES_DIR,
    *,
    gid: str | None = None,
    source_apk: Path | None = None,
) -> Path:
    if source_apk is not None and source_apk.is_file():
        return source_apk.resolve()

    packages_dir.mkdir(parents=True, exist_ok=True)
    gid = (gid or "").strip()
    if gid:
        return discover_source_apk_for_gid(gid, packages_dir)

    candidates = sorted(
        apk
        for apk in packages_dir.glob("*.apk")
        if apk.name != OUTPUT_APK_NAME and "gameturbo" not in apk.name.lower()
    )
    if len(candidates) != 1:
        names = ", ".join(path.name for path in candidates) or "none"
        raise RuntimeError(
            f"packages 目录必须且只能放一个原包 APK（排除 {OUTPUT_APK_NAME}），当前: {names}",
        )
    return candidates[0]


def discover_source_apk_for_gid(gid: str, packages_dir: Path = PACKAGES_DIR) -> Path:
    gid = (gid or "").strip()
    if not gid:
        raise RuntimeError("discover_source_apk_for_gid: empty gid")
    packages_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{gid}_"
    candidates = sorted(
        apk
        for apk in packages_dir.glob("*.apk")
        if apk.name.startswith(prefix) and "gameturbo" not in apk.name.lower()
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError(
            f"packages 中找不到 gid={gid} 的原包 APK（期望前缀 {prefix}*.apk）",
        )
    names = ", ".join(path.name for path in candidates)
    raise RuntimeError(
        f"packages 中 gid={gid} 的原包 APK 不唯一: {names}",
    )


def resolve_game_config(gid: str, games_dir: Path = GAMES_DIR) -> tuple[Path, bool]:
    games_dir.mkdir(parents=True, exist_ok=True)
    matches = sorted(games_dir.glob(f"gameturbo_{gid}_*.json"))
    if matches:
        return matches[0], False
    return init_game_config_from_template(gid, games_dir), True


def resolve_existing_game_config(gid: str, games_dir: Path = GAMES_DIR) -> Path:
    matches = sorted(games_dir.glob(f"gameturbo_{gid}_*.json"))
    if not matches:
        raise RuntimeError(
            f"gameturbo artifacts exist but no gameturbo_{gid}_*.json found",
        )
    return matches[0]


def init_game_config_from_template(gid: str, games_dir: Path = GAMES_DIR) -> Path:
    template_path = games_dir / "template.json"
    if not template_path.is_file():
        raise RuntimeError(f"GameTurbo config template not found: {template_path}")
    data = json.loads(template_path.read_text(encoding="utf-8"))
    data["game_id"] = gid
    target = games_dir / f"gameturbo_{gid}_test.json"
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    return target


def run_bootstrap_from_source(
    source_apk: Path,
    *,
    gid: str | None = None,
) -> GameTurboBootstrapResult:
    source_apk = source_apk.resolve()
    resolved_gid = (gid or "").strip() or parse_gid_from_apk_name(source_apk)
    game_config_path, created = resolve_game_config(resolved_gid)
    return GameTurboBootstrapResult(
        gid=resolved_gid,
        source_apk=source_apk,
        game_config_path=game_config_path,
        created_config=created,
    )
