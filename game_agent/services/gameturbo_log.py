from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from game_agent.services.adb_service import AdbService

logger = logging.getLogger(__name__)

GAMETURBO_LOG_FILENAME = "gameturbo.log"

_LOGCAT_TS_PATTERNS = (
    re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})"),  # 2026-05-20 11:27:56.837
    re.compile(r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})"),  # 05-20 11:27:56.837
)


def gameturbo_log_path(artifact_root: Path | None) -> Path:
    if artifact_root is not None:
        return artifact_root / GAMETURBO_LOG_FILENAME
    return Path(GAMETURBO_LOG_FILENAME)


def gameturbo_log_dedup_key(line: str) -> str:
    """去重键：仅行首 logcat 时间戳（毫秒），不按正文去重。

    间隔拉取 / 收尾 logcat -d 时，缓冲区会与上次快照重叠；
    重叠部分时间戳相同，只保留一份。时间不同则一律保留（如多条 [SEND-TUNNEL]）。
    无法解析时间戳的行（如 beginning of main）退化为整行键。
    """
    stripped = line.strip()
    for pattern in _LOGCAT_TS_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return match.group(1)
    return f"__no_ts__:{stripped}"


def fetch_device_gameturbo_lines(adb: AdbService, *, timeout_s: float = 60.0) -> list[str]:
    """读取设备 logcat 环形缓冲区中全部 GameTurbo 行（logcat -d，不清空）。"""
    try:
        result = subprocess.run(
            adb._base() + ["logcat", "-d", "-s", "GameTurbo"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("logcat -d -s GameTurbo 超时 (%.0fs)", timeout_s)
        return []
    lines: list[str] = []
    for raw in (result.stdout or "").splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
    return lines


def read_gameturbo_dedup_keys(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {
        gameturbo_log_dedup_key(line)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    }


def _count_nonempty_lines(path: Path) -> int:
    return sum(
        1
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    )


def _append_unique_lines(path: Path, lines: list[str], seen_keys: set[str]) -> int:
    added = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for line in lines:
            key = gameturbo_log_dedup_key(line)
            if key in seen_keys:
                continue
            f.write(line + "\n")
            seen_keys.add(key)
            added += 1
    return added


def clear_device_logcat(adb: AdbService, *, timeout_s: float = 15.0) -> None:
    """清空设备 logcat 环形缓冲区（会话重启后重新采集 GameTurbo 日志）。"""
    try:
        subprocess.run(
            adb._base() + ["logcat", "-c"],
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        logger.info("[GameTurboLog] 已执行 logcat -c")
    except subprocess.TimeoutExpired:
        logger.warning("[GameTurboLog] logcat -c 超时 (%.0fs)", timeout_s)
        raise


def _latest_session_archive(artifact_root: Path) -> Path | None:
    archives = sorted(artifact_root.glob("gameturbo_session_*.log"))
    return archives[-1] if archives else None


def ensure_gameturbo_log_for_analysis(artifact_root: Path) -> Path | None:
    """
    保证产物目录内存在非空的 gameturbo.log（分析脚本固定读取此文件名）。
    若当前文件为空且存在会话归档，则将最近一段归档复制为 gameturbo.log。
    仍无内容则返回 None。
    """
    path = gameturbo_log_path(artifact_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and _count_nonempty_lines(path) > 0:
        return path
    latest = _latest_session_archive(artifact_root)
    if latest is not None and _count_nonempty_lines(latest) > 0:
        shutil.copy2(latest, path)
        logger.info(
            "[GameTurboLog] 已将会话归档 %s 复制为 %s 供域名/日志分析",
            latest.name,
            GAMETURBO_LOG_FILENAME,
        )
    elif not path.is_file():
        path.write_text("", encoding="utf-8")
    if path.is_file() and _count_nonempty_lines(path) > 0:
        return path
    return None


def rotate_gameturbo_log(artifact_root: Path, *, session_index: int) -> Path | None:
    """
    将当前 gameturbo.log 内容复制归档为 gameturbo_session_NNN.log，并清空 gameturbo.log。
    始终保留 gameturbo.log 路径供后续写入与分析（不移动/改名主文件）。
    """
    active = gameturbo_log_path(artifact_root)
    if not active.is_file():
        active.parent.mkdir(parents=True, exist_ok=True)
        active.write_text("", encoding="utf-8")
        return None
    if _count_nonempty_lines(active) == 0:
        active.write_text("", encoding="utf-8")
        return None
    archived = artifact_root / f"gameturbo_session_{session_index:03d}.log"
    shutil.copy2(active, archived)
    active.write_text("", encoding="utf-8")
    logger.info(
        "[GameTurboLog] 已归档会话副本 %s（活跃文件仍为 %s）",
        archived.name,
        GAMETURBO_LOG_FILENAME,
    )
    return archived


def bootstrap_gameturbo_log(adb: AdbService, artifact_root: Path) -> Path:
    """观察者/会话开始时：将设备当前缓冲区写入 gameturbo.log（会话重启前应先 logcat -c）。"""
    path = gameturbo_log_path(artifact_root)
    lines = fetch_device_gameturbo_lines(adb)
    path.parent.mkdir(parents=True, exist_ok=True)
    if lines:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")
    logger.info(
        "[GameTurboLog] 初始快照已写入 %s | 设备缓冲区 %d 行",
        path,
        len(lines),
    )
    return path


def append_gameturbo_line(path: Path, line: str) -> None:
    stripped = line.strip()
    if not stripped:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(stripped + "\n")


def finalize_gameturbo_log(adb: AdbService, artifact_root: Path) -> Path | None:
    """运行结束时：把设备缓冲区中尚未落盘的行追加到 gameturbo.log。"""
    path = gameturbo_log_path(artifact_root)
    seen_keys = read_gameturbo_dedup_keys(path)
    dump_lines = fetch_device_gameturbo_lines(adb)

    if not seen_keys and dump_lines:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(dump_lines) + "\n", encoding="utf-8")
    else:
        added = _append_unique_lines(path, dump_lines, seen_keys)
        if added:
            logger.info("[GameTurboLog] 收尾追加设备缓冲区 %d 行", added)

    path = ensure_gameturbo_log_for_analysis(artifact_root)
    if path is None:
        logger.warning(
            "[GameTurboLog] 未收集到 GameTurbo 日志: %s",
            gameturbo_log_path(artifact_root),
        )
        return None

    total = _count_nonempty_lines(path)

    logger.info(
        "[GameTurboLog] 归档完成 %s | 共 %d 行",
        GAMETURBO_LOG_FILENAME,
        total,
    )
    return path
