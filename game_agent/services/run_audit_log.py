from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)

from game_agent.services.llm_transcript import (
    format_new_llm_messages,
    format_user_parts_for_console,
)
from game_agent.utils.stage_logging import attach_stage_formatter

logger = logging.getLogger(__name__)

_INDEX_FILE = "index.json"


@dataclass
class RunAuditLogger:
    """
    单次 retry（或独立 run）的 AI 审计日志。
    产出目录：<artifact_root>/audit/
      - events.jsonl   结构化事件（便于检索/分析）
      - ai_trace.md    人类可读时间线
      - index.json     摘要索引
    """

    artifact_root: Path
    enabled: bool = True
    _event_count: int = field(default=0, init=False)
    _jsonl_path: Path | None = field(default=None, init=False)
    _md_path: Path | None = field(default=None, init=False)
    _index: dict[str, Any] = field(default_factory=dict, init=False)
    _process_log_handler: logging.Handler | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        audit_dir = (self.artifact_root / "audit").resolve()
        audit_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_path = audit_dir / "events.jsonl"
        self._md_path = audit_dir / "ai_trace.md"
        if not self._md_path.is_file():
            self._md_path.write_text(
                "# AI 运行审计日志\n\n"
                "记录主脑思考、工具调用与返回；JSON 见 `events.jsonl`。\n\n",
                encoding="utf-8",
            )
        self._index = {
            "artifact_root": str(self.artifact_root),
            "started_at": _now_iso(),
            "events": 0,
            "thinking_count": 0,
            "tool_call_count": 0,
            "tool_return_count": 0,
            "phases": [],
        }
        self._persist_index()

    def attach_process_log_handler(self, level: str = "INFO") -> logging.Handler | None:
        """将标准 logging 同时写入 artifact_root/process.log。"""
        if not self.enabled:
            return None
        self.detach_process_log_handler()
        log_path = self.artifact_root / "process.log"
        handler = logging.FileHandler(log_path, encoding="utf-8")
        attach_stage_formatter(handler, level)
        logging.getLogger().addHandler(handler)
        self._process_log_handler = handler
        return handler

    def detach_process_log_handler(self) -> None:
        """关闭并移除 process.log FileHandler，便于任务结束后删除 artifacts。"""
        handler = self._process_log_handler
        if handler is None:
            return
        root = logging.getLogger()
        try:
            handler.flush()
            handler.close()
        except OSError as e:
            logger.debug("Ignored error closing process.log handler: %s", e)
        if handler in root.handlers:
            root.removeHandler(handler)
        self._process_log_handler = None

    def log_phase(self, phase: str, message: str, **extra: Any) -> None:
        self._emit("phase", phase, message=message, **extra)

    def log_round_start(self, phase: str, round_id: int, *, note: str = "") -> None:
        self._emit("round_start", phase, round_id=round_id, note=note)
        self._append_md(f"\n## {_now_iso()} | {phase} | 第 {round_id + 1} 轮\n")
        if note:
            self._append_md(f"\n{note}\n")

    def log_user_prompt(
        self,
        phase: str,
        round_id: int,
        user_parts: list[str],
        *,
        max_chars: int = 12000,
    ) -> None:
        text = "\n".join(p for p in user_parts if isinstance(p, str))
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n…[截断，原长 {len(text)} 字符]"
        self._emit("user_prompt", phase, round_id=round_id, text=text)
        self._append_md(f"\n### 发往模型的上下文（轮次 {round_id + 1}）\n\n```\n{text}\n```\n")

    def log_llm_messages(self, phase: str, round_id: int, new_messages: list[ModelMessage]) -> None:
        """按时间顺序记录思考、文本、工具调用与工具返回。"""
        for msg in new_messages:
            for part in getattr(msg, "parts", ()) or ():
                if isinstance(part, ThinkingPart):
                    content = (part.content or "").strip()
                    self._emit(
                        "thinking",
                        phase,
                        round_id=round_id,
                        thinking_id=part.id,
                        content=content,
                    )
                    self._index["thinking_count"] = int(self._index.get("thinking_count", 0)) + 1
                    self._append_md(f"\n### 思考（轮次 {round_id + 1}）\n\n{content}\n")
                elif isinstance(part, TextPart):
                    content = (part.content or "").strip()
                    self._emit("model_text", phase, round_id=round_id, content=content)
                    self._append_md(f"\n### 模型文本输出（轮次 {round_id + 1}）\n\n{content}\n")
                elif isinstance(part, ToolCallPart):
                    args = part.args if isinstance(part.args, str) else repr(part.args)
                    self._emit(
                        "tool_call",
                        phase,
                        round_id=round_id,
                        tool=part.tool_name,
                        tool_call_id=part.tool_call_id,
                        args=args,
                    )
                    self._index["tool_call_count"] = int(self._index.get("tool_call_count", 0)) + 1
                    self._append_md(
                        f"\n### 工具调用 `{part.tool_name}`（轮次 {round_id + 1}）\n\n"
                        f"```\n{args}\n```\n",
                    )
                elif isinstance(part, ToolReturnPart):
                    ret = part.content
                    rtxt = ret if isinstance(ret, str) else repr(ret)
                    if len(rtxt) > 8000:
                        rtxt = rtxt[:8000] + "…[截断]"
                    self._emit(
                        "tool_return",
                        phase,
                        round_id=round_id,
                        tool=part.tool_name,
                        content=rtxt,
                    )
                    self._index["tool_return_count"] = int(self._index.get("tool_return_count", 0)) + 1
                    self._append_md(
                        f"\n### 工具返回 `{part.tool_name}`（轮次 {round_id + 1}）\n\n"
                        f"```\n{rtxt}\n```\n",
                    )
        self._persist_index()

    def log_tool(
        self,
        phase: str,
        round_id: int | None,
        tool_name: str,
        args: Any,
        result: str,
        *,
        source: str = "runtime",
    ) -> None:
        """工具执行时即时写入（与 log_llm_messages 互补，便于崩溃后仍保留记录）。"""
        arg_s = args if isinstance(args, str) else repr(args)
        if len(arg_s) > 4000:
            arg_s = arg_s[:4000] + "…"
        res = result if len(result) <= 8000 else result[:8000] + "…"
        self._emit(
            "tool_exec",
            phase,
            round_id=round_id,
            tool=tool_name,
            args=arg_s,
            result=res,
            source=source,
        )

    def log_transcript_bundle(
        self,
        phase: str,
        round_id: int,
        user_parts: list[str],
        new_messages: list[ModelMessage],
    ) -> None:
        """一轮结束：写入用户消息摘要 + 完整 LLM 结构化转写文件。"""
        audit_dir = self.artifact_root / "audit"
        bundle_path = audit_dir / f"round_{round_id:03d}_transcript.txt"
        body = (
            f"=== 用户消息 ===\n{format_user_parts_for_console(user_parts)}\n\n"
            f"=== LLM 新增消息 ===\n{format_new_llm_messages(new_messages)}\n"
        )
        try:
            bundle_path.write_text(body, encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to write transcript: %s", e)
        self.log_user_prompt(phase, round_id, user_parts)
        self.log_llm_messages(phase, round_id, new_messages)

    def log_observer(
        self,
        *,
        kind: str,
        message: str,
        round_id: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._emit(kind, "observer", round_id=round_id, message=message, **(extra or {}))

    def finalize(self, *, success: bool, note: str = "") -> None:
        if not self.enabled:
            return
        self._emit("finalize", "orchestrator", success=success, note=note[:4000])
        self._index["finished_at"] = _now_iso()
        self._index["success"] = success
        self._index["note"] = note[:2000]
        self._persist_index()

    def _emit(self, event_type: str, phase: str, **data: Any) -> None:
        if not self.enabled or self._jsonl_path is None:
            return
        rec: dict[str, Any] = {
            "ts": _now_iso(),
            "phase": phase,
            "type": event_type,
            **data,
        }
        try:
            with self._jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("Failed to write audit jsonl: %s", e)
            return
        self._event_count += 1
        self._index["events"] = self._event_count
        phases: list[str] = self._index.setdefault("phases", [])
        if phase not in phases:
            phases.append(phase)

    def _append_md(self, text: str) -> None:
        if not self.enabled or self._md_path is None:
            return
        try:
            with self._md_path.open("a", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            logger.warning("Failed to write ai_trace.md: %s", e)

    def _persist_index(self) -> None:
        if not self.enabled:
            return
        path = self.artifact_root / "audit" / _INDEX_FILE
        try:
            path.write_text(json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to write audit index: %s", e)


def _now_iso() -> str:
    return datetime.now(tz=UTC).astimezone().isoformat(timespec="seconds")
