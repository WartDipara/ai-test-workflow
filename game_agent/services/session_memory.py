from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ToolCallPart

logger = logging.getLogger(__name__)

HISTORY_FILE = "conversation_history.json"
MEMORY_FILE = "session_memory.json"
_MAX_ACTION_LINES = 40
_MAX_ARG_CHARS = 120


@dataclass
class RoundRecord:
    round_id: int
    tools: list[str] = field(default_factory=list)


@dataclass
class SessionMemory:
    """单次 run（artifacts/run_*）内的操作记录；由 Controller 读写，主脑不参与管理。"""

    session_id: str
    started_at: str
    rounds: list[RoundRecord] = field(default_factory=list)

    def append_round(self, *, round_id: int, new_messages: list[ModelMessage]) -> None:
        self.rounds.append(
            RoundRecord(round_id=round_id, tools=summarize_tool_calls(new_messages)),
        )

    def format_action_log(self) -> str:
        """仅事实性工具链列表，不含任何给模型的指令性话术。"""
        if not self.rounds:
            return "(none)"
        return "\n".join(
            f"R{rec.round_id + 1}: "
            + (" → ".join(rec.tools) if rec.tools else "-")
            for rec in self.rounds[-_MAX_ACTION_LINES:]
        )


def summarize_tool_calls(messages: list[ModelMessage]) -> list[str]:
    out: list[str] = []
    for msg in messages:
        for part in getattr(msg, "parts", ()) or ():
            if not isinstance(part, ToolCallPart):
                continue
            args = part.args
            arg_s = args if isinstance(args, str) else repr(args)
            if len(arg_s) > _MAX_ARG_CHARS:
                arg_s = arg_s[:_MAX_ARG_CHARS] + "…"
            out.append(f"{part.tool_name}({arg_s})")
    return out


def save_session_memory(path: Path, memory: SessionMemory) -> None:
    try:
        path.write_text(
            json.dumps(asdict(memory), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("写入 session_memory 失败: %s", e)


def load_session_memory(path: Path) -> SessionMemory | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rounds = [RoundRecord(**r) for r in data.get("rounds", [])]
        return SessionMemory(
            session_id=data.get("session_id", ""),
            started_at=data.get("started_at", ""),
            rounds=rounds,
        )
    except (OSError, json.JSONDecodeError, TypeError) as e:
        logger.warning("读取 session_memory 失败: %s", e)
        return None


def save_conversation_history(path: Path, history: list[ModelMessage]) -> None:
    try:
        path.write_bytes(ModelMessagesTypeAdapter.dump_json(history))
    except OSError as e:
        logger.warning("写入 conversation_history 失败: %s", e)


def load_conversation_history(path: Path) -> list[ModelMessage] | None:
    if not path.is_file():
        return None
    try:
        return ModelMessagesTypeAdapter.validate_json(path.read_bytes())
    except (OSError, ValueError) as e:
        logger.warning("读取 conversation_history 失败: %s", e)
        return None


def new_session_memory(session_id: str) -> SessionMemory:
    return SessionMemory(
        session_id=session_id,
        started_at=datetime.now(tz=timezone.utc).isoformat(),
    )
