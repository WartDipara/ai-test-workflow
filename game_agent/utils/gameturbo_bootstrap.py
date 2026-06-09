from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from game_agent.paths import GAMETURBO_MERGED_CONFIG_PATH, REPO_ROOT, gameturbo_merged_config_path

logger = logging.getLogger(__name__)

PACKAGES_DIR = REPO_ROOT / "GameTurbo-Native" / "client" / "android" / "packages"
GAMES_DIR = REPO_ROOT / "GameTurbo-Native" / "games"
OUTPUT_APK_NAME = "game_gameturbo.apk"


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
    """单轮 retry 产物目录下的 deploy 合并配置路径。"""
    safe_gid = (gid or "").strip() or "unknown"
    return artifact_root.resolve() / f".gameturbo_merged_{safe_gid}.json"


def resolve_merged_config_deploy_path(
    gid: str,
    *,
    artifact_root: Path | None = None,
    merged_config_output: Path | None = None,
) -> Path | None:
    """
    deploy.sh -m 写入路径。
    有 artifact_root 时直接写入轮次产物，避免在 GameTurbo-Native 根目录堆积 .gameturbo_merged_*.json。
    """
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
    """
    deploy 完成后确保合并配置落在 target；若仍留在 GameTurbo-Native 则移动并清理残留。
    """
    gid = (gid or "").strip()
    target = target.resolve()
    native = gameturbo_merged_config_path(gid) if gid else GAMETURBO_MERGED_CONFIG_PATH

    if target.is_file():
        if native.is_file() and native.resolve() != target.resolve():
            try:
                native.unlink()
                logger.info("已删除 GameTurbo-Native 合并配置残留: %s", native)
            except OSError as exc:
                logger.warning("删除合并配置残留失败 %s: %s", native, exc)
        return target

    if native.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(native), str(target))
        logger.info("已将合并配置移至产物目录: %s", target)
        return target

    raise RuntimeError(
        f"deploy 未生成合并配置（期望 {target} 或 {native}）",
    )


def find_merged_config_for_deliverable(
    gid: str,
    *,
    winning_artifact_root: Path | None = None,
) -> Path | None:
    """成功交付时定位合并配置：优先 winning retry 产物，再回退 GameTurbo-Native。"""
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

_GID_RE = re.compile(r"^(\d+)")


@dataclass(frozen=True, slots=True)
class GameTurboBootstrapResult:
    gid: str
    source_apk: Path
    game_config_path: Path
    created_config: bool


def output_apk_path(gid: str | None = None) -> Path:
    return PACKAGES_DIR / output_apk_name(gid)


def needs_initial_preprocess(gid: str | None = None) -> bool:
    """本地尚无 gameturbo 产物 APK 时需完整 bootstrap。"""
    return not output_apk_path(gid).is_file()


def needs_gameturbo_deploy(
    output_apk: Path,
    *,
    package_installed: bool,
) -> bool:
    """
    是否需要执行 deploy（build/inject/install）。
    设备已安装时，不因本地产物 APK 被清理而重复打包（Modify 重试常见）。
    """
    if package_installed:
        return False
    if not output_apk.is_file():
        return True
    return True


def peek_gid_from_packages(packages_dir: Path = PACKAGES_DIR) -> str | None:
    """在仅有原包、尚无 gameturbo 产物时，从原包文件名解析 gid。"""
    try:
        return parse_gid_from_apk_name(discover_source_apk(packages_dir))
    except RuntimeError:
        return None


def resolve_task_gid(gid: str = "", packages_dir: Path = PACKAGES_DIR) -> str:
    resolved = (gid or "").strip()
    if resolved:
        return resolved
    peeked = peek_gid_from_packages(packages_dir)
    if peeked:
        return peeked
    return "unknown"


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
        names = ", ".join(path.name for path in candidates) or "无"
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


def parse_gid_from_apk_name(apk_path: Path) -> str:
    match = _GID_RE.match(apk_path.name)
    if not match:
        raise RuntimeError(
            f"无法从 APK 文件名解析 gid: {apk_path.name}。"
            "文件名需以数字 gid 开头（如 12345_game.apk）。",
        )
    return match.group(1)


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
            f"已存在 gameturbo 产物，但找不到 gameturbo_{gid}_*.json",
        )
    return matches[0]


def init_game_config_from_template(gid: str, games_dir: Path = GAMES_DIR) -> Path:
    template_path = games_dir / "template.json"
    if not template_path.is_file():
        raise RuntimeError(f"找不到 GameTurbo 配置模板: {template_path}")
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
