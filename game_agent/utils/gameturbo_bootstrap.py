from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from game_agent.paths import REPO_ROOT
from game_agent.utils.settings_yaml import upsert_top_level_section_fields

PACKAGES_DIR = REPO_ROOT / "GameTurbo-Native" / "client" / "android" / "packages"
GAMES_DIR = REPO_ROOT / "GameTurbo-Native" / "games"
OUTPUT_APK_NAME = "game_gameturbo.apk"

_GID_RE = re.compile(r"^(\d+)")


@dataclass(frozen=True, slots=True)
class GameTurboBootstrapResult:
    gid: str
    source_apk: Path
    game_config_path: Path
    created_config: bool


def output_apk_path() -> Path:
    return PACKAGES_DIR / OUTPUT_APK_NAME


def needs_initial_preprocess() -> bool:
    return not output_apk_path().is_file()


def peek_gid_from_packages(packages_dir: Path = PACKAGES_DIR) -> str | None:
    """在仅有原包、尚无 game_gameturbo.apk 时，从原包文件名解析 gid。"""
    try:
        return parse_gid_from_apk_name(discover_source_apk(packages_dir))
    except RuntimeError:
        return None


def resolve_task_gid(cfg_gid: str, packages_dir: Path = PACKAGES_DIR) -> str:
    gid = (cfg_gid or "").strip()
    if gid:
        return gid
    peeked = peek_gid_from_packages(packages_dir)
    if peeked:
        return peeked
    return "unknown"


def discover_source_apk(packages_dir: Path = PACKAGES_DIR) -> Path:
    packages_dir.mkdir(parents=True, exist_ok=True)
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


def parse_gid_from_apk_name(apk_path: Path) -> str:
    match = _GID_RE.match(apk_path.name)
    if not match:
        raise RuntimeError(f"无法从 APK 文件名解析 gid: {apk_path.name}")
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
            f"当前已存在 {OUTPUT_APK_NAME}，属于修改阶段；但找不到 gameturbo_{gid}_*.json",
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


def run_bootstrap(settings_path: Path) -> GameTurboBootstrapResult:
    source_apk = discover_source_apk()
    gid = parse_gid_from_apk_name(source_apk)
    game_config_path, created = resolve_game_config(gid)
    persist_gameturbo_context(
        settings_path,
        gid=gid,
        game_config_path=game_config_path,
        source_apk=source_apk,
    )
    return GameTurboBootstrapResult(
        gid=gid,
        source_apk=source_apk,
        game_config_path=game_config_path,
        created_config=created,
    )


def persist_gameturbo_context(
    settings_path: Path,
    *,
    gid: str,
    game_config_path: Path,
    source_apk: Path,
) -> None:
    upsert_top_level_section_fields(
        settings_path,
        "gameturbo",
        {
            "gid": gid,
            "game_config_path": str(game_config_path.resolve()),
            "source_apk": str(source_apk.resolve()),
        },
    )

