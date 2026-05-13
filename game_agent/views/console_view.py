from __future__ import annotations

import logging
from typing import Any


class ConsoleView:
    """简单控制台视图：与业务 Model 分离，仅负责呈现。"""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("game_agent.view")

    def banner(self, text: str) -> None:
        self._log.info("==== %s ====", text)

    def round(self, index: int, phase: str) -> None:
        self._log.info("— Round %s | %s —", index, phase)

    def tool(self, name: str, detail: str) -> None:
        self._log.info("[tool] %s | %s", name, detail)

    def model_output(self, text: str) -> None:
        self._log.info("[model] %s", text[:4000])

    def error(self, msg: str, exc_info: Any = None) -> None:
        self._log.error(msg, exc_info=exc_info)
