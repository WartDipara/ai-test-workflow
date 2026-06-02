from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from game_agent.models.settings import AppConfig
from game_agent.modules.observer_session.state import ObserverSessionState
from game_agent.services.adb_service import AdbService
from game_agent.services.run_audit_log import RunAuditLogger
from game_agent.utils.ocr_util import extract_text_with_bounds
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)

# 仅当画面异常原因包含以下网络相关关键词时，才视为真正的失败。
# 非网络错误（如账号密码错误）不应触发失败/重试流程。
_NETWORK_ANOMALY_WHITELIST = (
    "网络连接失败", "网络异常", "网络无连接", "没有网络", "请检查网络",
    "连接超时", "连接失败", "服务器连接失败", "与服务器断开连接",
    "服务器加载失败", "服务器获取失败", "服务器繁忙", "服务器维护中",
    "资源下载失败", "资源加载失败", "更新失败", "下载失败",
    "当前地区不支持", "当前区域暂未开放",
)


def _is_network_anomaly(reason: str) -> bool:
    """判断画面异常原因是否属于网络相关错误。"""
    for keyword in _NETWORK_ANOMALY_WHITELIST:
        if keyword in reason:
            return True
    return False


@dataclass(slots=True)
class ScreenMonitor:
    """定时截图 + 多模态画面分析模块。"""

    adb: AdbService
    app_config: AppConfig
    artifact_root: Path
    session_state: ObserverSessionState | None = None
    audit: RunAuditLogger | None = None
    shot_interval_s: float = 10.0
    download_stuck_rounds: int = 10

    async def run_until_anomaly(self, stop_event: asyncio.Event) -> str | None:
        cfg = self.app_config
        llm_cfg = cfg.llm_multimodal or cfg.llm
        logger.info(
            "[ScreenMonitor] 开始监控 | 间隔 %.0fs | 多模态模型 %s",
            self.shot_interval_s,
            llm_cfg.model_name,
        )
        vision_worker = VisionWorker(llm_cfg)
        session = self.session_state
        total_rounds = 0

        while not stop_event.is_set():
            if session is not None and not session.monitoring_enabled:
                break

            total_rounds += 1
            if session is not None:
                session.screen_round_in_session += 1
                round_in_session = session.screen_round_in_session
                session_index = session.session_index
                stuck_count = session.screen_stuck_count
                last_progress = session.screen_last_progress
            else:
                round_in_session = total_rounds
                session_index = 1
                stuck_count = 0
                last_progress = ""

            logger.info(
                "[ScreenMonitor] s%02d 第 %d 轮 | %.0fs 后截图...",
                session_index,
                round_in_session,
                self.shot_interval_s,
            )
            await asyncio.sleep(self.shot_interval_s)
            if stop_event.is_set():
                break
            if session is not None and not session.monitoring_enabled:
                break

            shot_path = (
                self.artifact_root
                / f"monitor_screen_s{session_index:02d}_{round_in_session:03d}.png"
            )
            t0 = time.perf_counter()
            try:
                self.adb.screencap_png(shot_path)
                size_kb = shot_path.stat().st_size / 1024 if shot_path.is_file() else 0
                logger.info(
                    "[ScreenMonitor] s%02d 第 %d 轮截图 | %s | %.1f KB | %.2fs",
                    session_index,
                    round_in_session,
                    shot_path.name,
                    size_kb,
                    time.perf_counter() - t0,
                )
            except Exception as e:
                logger.warning(
                    "[ScreenMonitor] s%02d 第 %d 轮截图失败: %s",
                    session_index,
                    round_in_session,
                    e,
                )
                continue

            try:
                ocr_summary = extract_text_with_bounds(shot_path)
            except Exception as e:
                ocr_summary = f"[OCR 识别失败] {e}"

            try:
                state_json_str = await vision_worker.analyze_game_state(
                    screenshot_path=shot_path,
                    ocr_summary=ocr_summary,
                    round_id=round_in_session,
                )
                if state_json_str.startswith("```json"):
                    state_json_str = state_json_str[7:]
                if state_json_str.endswith("```"):
                    state_json_str = state_json_str[:-3]
                state = json.loads(state_json_str)
                logger.info(
                    "[ScreenMonitor] s%02d 第 %d 轮画面: %s",
                    session_index,
                    round_in_session,
                    state,
                )
                if self.audit is not None:
                    self.audit.log_observer(
                        kind="screen_state",
                        message=str(state),
                        round_id=total_rounds,
                        extra={
                            **(state if isinstance(state, dict) else {}),
                            "session_index": session_index,
                        },
                    )

                if state.get("has_anomaly"):
                    reason = state.get("anomaly_reason", "未知画面异常")
                    if _is_network_anomaly(reason):
                        logger.warning(
                            "[ScreenMonitor] s%02d 网络异常: %s",
                            session_index,
                            reason,
                        )
                        if self.audit is not None:
                            self.audit.log_observer(
                                kind="screen_anomaly",
                                message=reason,
                                round_id=total_rounds,
                            )
                        return f"Screen anomaly detected: {reason}"
                    else:
                        logger.info(
                            "[ScreenMonitor] s%02d 非网络画面异常，忽略: %s",
                            session_index,
                            reason,
                        )
                        if self.audit is not None:
                            self.audit.log_observer(
                                kind="screen_anomaly_ignored",
                                message=f"非网络异常已忽略: {reason}",
                                round_id=total_rounds,
                            )

                stage = state.get("stage", "unknown")
                progress = state.get("progress", "")
                if stage == "resource_download" and progress:
                    if progress == last_progress:
                        stuck_count += 1
                        if session is not None:
                            session.screen_stuck_count = stuck_count
                        logger.info(
                            "[ScreenMonitor] 下载进度未变 (%s) | %d/%d",
                            progress,
                            stuck_count,
                            self.download_stuck_rounds,
                        )
                        if stuck_count >= self.download_stuck_rounds:
                            return (
                                "Screen anomaly detected: "
                                f"Resource download stuck at {progress}"
                            )
                    else:
                        last_progress = progress
                        stuck_count = 0
                        if session is not None:
                            session.screen_last_progress = last_progress
                            session.screen_stuck_count = 0
                else:
                    stuck_count = 0
                    if session is not None:
                        session.screen_stuck_count = 0
            except json.JSONDecodeError as e:
                logger.warning(
                    "[ScreenMonitor] s%02d 非 JSON: %s",
                    session_index,
                    e,
                )
            except Exception as e:
                logger.warning(
                    "[ScreenMonitor] s%02d 分析失败: %s",
                    session_index,
                    e,
                )
        return None
