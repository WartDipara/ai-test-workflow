"""从 YAML 加载应用配置，并支持 ${ENV_VAR} 环境变量展开。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from game_agent.models.settings import AppConfig

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def expand_env_strings(obj: Any) -> Any:
    """递归展开字符串中的 ${VAR}；未设置则保留占位原文以便 pydantic 报错。"""

    if isinstance(obj, str):

        def repl(m: re.Match[str]) -> str:
            key = m.group(1)
            return os.environ.get(key, m.group(0))

        return _ENV_PATTERN.sub(repl, obj)
    if isinstance(obj, dict):
        return {k: expand_env_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env_strings(i) for i in obj]
    return obj


def load_app_config(path: Path) -> AppConfig:
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ValueError(
            f"Failed to parse config YAML: {path}. "
            "Check for unescaped backslashes in paths (use \\\\ or / on Windows)."
        ) from e
    expanded = expand_env_strings(raw)
    return AppConfig.model_validate(expanded)
