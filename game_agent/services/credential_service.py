from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class Credentials:
    username: str
    password: str


class CredentialService:
    """从独立文件读取凭据；不向日志打印密码。"""

    def __init__(self, file_path: Path) -> None:
        self._path = file_path

    def load(self) -> Credentials:
        if not self._path.is_file():
            raise FileNotFoundError(f"凭据文件不存在: {self._path}")
        data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        user = data.get("username")
        pwd = data.get("password")
        if not isinstance(user, str) or not isinstance(pwd, str):
            raise ValueError("credentials 文件需包含字符串字段 username 与 password")
        return Credentials(username=user, password=pwd)
