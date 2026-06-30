"""从 YAML 加载游戏登录凭据（username / password / sub_account）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from game_agent.paths import REPO_ROOT

_DEFAULT_SUB_ACCOUNT_PHRASES: tuple[str, ...] = (
    "小号1",
    "小号一",
    "小號1",
    "小號一",
    "sub-account 1",
)


@dataclass(frozen=True, slots=True)
class GameCredentials:
    username: str
    password: str
    sub_account: str | None = None


def _expand_sub_account_phrase_variants(phrase: str) -> tuple[str, ...]:
    base = (phrase or "").strip()
    if not base:
        return ()
    seen: set[str] = {base}
    out: list[str] = [base]
    collapsed = re.sub(r"\s+", "", base)
    if collapsed not in seen:
        seen.add(collapsed)
        out.append(collapsed)
    spaced = re.sub(r"(\D)(\d)", r"\1 \2", collapsed)
    if spaced not in seen:
        seen.add(spaced)
        out.append(spaced)
    fw = collapsed.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
    if fw not in seen:
        seen.add(fw)
        out.append(fw)
    return tuple(out)


def resolve_sub_account_match_phrases(cred: GameCredentials | None) -> tuple[str, ...]:
    """凭据指定小号目标；未配置时用默认三语别名（英文匹配时大小写不敏感）。"""
    if cred is not None and (cred.sub_account or "").strip():
        return _expand_sub_account_phrase_variants(cred.sub_account or "")
    return _DEFAULT_SUB_ACCOUNT_PHRASES


def sub_account_target_display(cred: GameCredentials | None) -> str:
    """日志 / VLM prompt 用的人类可读目标。"""
    if cred is not None and (cred.sub_account or "").strip():
        return (cred.sub_account or "").strip()
    return "小号1 / sub-account 1 (default)"


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
            f"Credentials file not found: {path} (copy credentials.example.yaml under config/)"
        )

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Credentials file must be YAML map: {path}")

    username = (
        raw.get("username")
        or raw.get("account")
        or raw.get("user")
        or ""
    )
    password = raw.get("password") or raw.get("pass") or raw.get("pwd") or ""
    sub_account_raw = (
        raw.get("sub_account")
        or raw.get("sub_account_label")
        or raw.get("小号")
        or raw.get("小號")
        or ""
    )
    sub_account = str(sub_account_raw).strip() or None

    if not str(username).strip():
        raise ValueError(f"Credentials file missing username (or account/user): {path}")
    if not str(password).strip():
        raise ValueError(f"Credentials file missing password: {path}")

    return GameCredentials(
        username=str(username).strip(),
        password=str(password).strip(),
        sub_account=sub_account,
    )


def try_load_game_credentials(
    file_path: Path,
    *,
    settings_path: Path | None = None,
) -> GameCredentials | None:
    try:
        return load_game_credentials(file_path, settings_path=settings_path)
    except (FileNotFoundError, ValueError):
        return None


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
    sub = sub_account_target_display(cred)
    return (
        f"Credentials loaded: username={masked_user}, sub_account={sub!r}. "
        "Login: LangGraph atomic_login reads credentials.yaml → OCR coords → u2 fill → ENTER/submit → OCR verify."
    )
