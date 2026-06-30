from __future__ import annotations

import logging
from typing import Any


class ConsoleView:
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

    def llm_user_bundle(self, round_index: int, text: str) -> None:
        """打印本轮构造后、即将发给 agent.run 的用户侧内容（截图 Base64 已折叠）。"""
        self._log.info(
            "<<<<<<<<<< Round %s 发往 LLM 的用户消息（见下） <<<<<<<<<<\n%s\n"
            ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>",
            round_index,
            text,
        )

    def llm_response_bundle(self, round_index: int, text: str) -> None:
        """打印本轮 LLM 新增消息：思考链、工具调用、工具返回、文本等。"""
        self._log.info(
            "++++++++++ Round %s LLM 本轮新增消息（含思考/工具）++++++++++\n%s\n"
            "++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++",
            round_index,
            text,
        )

    def llm_raw_messages_json(self, round_index: int, text: str) -> None:
        """打印 SDK 视角的原始 new_messages_json，便于确认是否返回 reasoning_content。"""
        self._log.info(
            "~~~~~~~~~~ Round %s 原始 new_messages_json（截断）~~~~~~~~~~\n%s\n"
            "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~",
            round_index,
            text[:20000],
        )

    def error(self, msg: str, exc_info: Any = None) -> None:
        self._log.error(msg, exc_info=exc_info)
