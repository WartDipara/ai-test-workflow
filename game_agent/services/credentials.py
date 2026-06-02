"""从 YAML 加载游戏登录凭据（username / password）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from game_agent.paths import REPO_ROOT


@dataclass(frozen=True, slots=True)
class GameCredentials:
    username: str
    password: str


def resolve_credentials_path(file_path: Path, *, settings_path: Path | None = None) -> Path:
    """解析凭据文件路径；相对路径依次尝试仓库根、settings 所在 config 目录。"""
    p = Path(file_path)
    if p.is_absolute():
        return p.resolve()

    bases: list[Path] = [REPO_ROOT]
    if settings_path is not None:
        sp = settings_path.resolve().parent
        bases.extend([sp.parent, sp])

    seen: set[Path] = set()
    for base in bases:
        base = base.resolve()
        if base in seen:
            continue
        seen.add(base)
        candidate = (base / p).resolve()
        if candidate.is_file():
            return candidate

    return (REPO_ROOT / p).resolve()


def load_game_credentials(
    file_path: Path,
    *,
    settings_path: Path | None = None,
) -> GameCredentials:
    path = resolve_credentials_path(file_path, settings_path=settings_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"凭据文件不存在: {path}（可在 config/ 下复制 credentials.example.yaml）"
        )

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"凭据文件格式错误（应为 YAML 映射）: {path}")

    username = (
        raw.get("username")
        or raw.get("account")
        or raw.get("user")
        or ""
    )
    password = raw.get("password") or raw.get("pass") or raw.get("pwd") or ""

    if not str(username).strip():
        raise ValueError(f"凭据文件缺少 username（或 account/user）: {path}")
    if not str(password).strip():
        raise ValueError(f"凭据文件缺少 password: {path}")

    return GameCredentials(username=str(username).strip(), password=str(password).strip())


def credentials_status_message(
    file_path: Path,
    *,
    settings_path: Path | None = None,
) -> str:
    """供主脑判断能否自动填表；不输出密码明文。"""
    try:
        cred = load_game_credentials(file_path, settings_path=settings_path)
    except FileNotFoundError as e:
        return f"Credentials not configured: {e}"
    except ValueError as e:
        return f"Invalid credentials: {e}"

    user = cred.username
    if len(user) > 4:
        masked_user = user[:2] + "***" + user[-2:]
    else:
        masked_user = "***"
    return (
        f"Credentials loaded: username={masked_user} (full value only via fill_credential_field on device). "
        "At login with account/password fields: OCR the field center, then "
        "fill_credential_field(x, y, field='username'|'password'); tool clears then fills."
    )
