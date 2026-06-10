from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryImage

from game_agent.models.game_entry_judgment import GameEntryJudgment
from game_agent.models.settings import LLMSection
from game_agent.services.llm_service import build_llm_model

logger = logging.getLogger(__name__)


class VisionWorker:
    """多模态：只负责观察、分析、汇报，不直接执行设备操作。"""

    def __init__(self, llm_config: LLMSection) -> None:
        self._llm_config = llm_config
        self._agent = Agent(build_llm_model(llm_config), output_type=str)

    async def analyze_game_state(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        round_id: int | None = None,
    ) -> str:
        """
        用于监控游戏运行状态（如下载、登录、进入游戏）及异常情况。
        返回一段 JSON 格式的字符串，包含 status、stage、message 等字段。
        """
        prompt = f"""
Analyze the current game screen state and whether a **network-related** anomaly is shown.
OCR:
{ocr_summary}

Tasks:
1. Stage:
   - resource_download: asset download (progress bar)
   - login: login / server list UI
   - enter_game: in game or connecting to game server
   - unknown
2. has_anomaly=true **only** for network error dialogs/copy, e.g. (including Chinese UI if present):
   network failed, no network, check network, connection timeout/failed, server connection failed/disconnected,
   server load/fetch failed, server busy/maintenance, resource download/load failed, update/download failed,
   region not supported / not open in this area.
   **Important** has_anomaly=false for:
   wrong account/password, login failed, captcha, account frozen; any account/verification/real-name errors;
   server picker, queue, normal loading; privacy/terms/announcement/event popups.
3. If resource_download, extract progress percent if visible.
4. **Ignore top-left GameTurbo acceleration overlay** (speed/MB/s/Mbps, 加速, 网速角标) — it is NOT download progress.
   Only count center/bottom resource download bar or explicit download percentage.

Return valid JSON only (no markdown fence):
{{
    "has_anomaly": bool,
    "anomaly_reason": "reason if anomaly else empty",
    "stage": "resource_download | login | enter_game | unknown",
    "progress": "e.g. 45% if downloading else empty"
}}
"""
        prefix = f"[VisionWorker] 第 {round_id} 轮" if round_id is not None else "[VisionWorker]"
        model = self._llm_config.model_name
        logger.info(
            "%s 请求多模态 API | model=%s | 截图=%s",
            prefix,
            model,
            screenshot_path.name,
        )
        t0 = time.perf_counter()
        try:
            result = await self._agent.run([prompt, BinaryImage.from_path(screenshot_path)])
            output = (result.output or "").strip()
            elapsed = time.perf_counter() - t0
            preview = output.replace("\n", " ")[:240]
            logger.info(
                "%s API 返回 | 耗时 %.2fs | 输出预览: %s%s",
                prefix,
                elapsed,
                preview,
                "..." if len(output) > 240 else "",
            )
            return output
        except Exception:
            elapsed = time.perf_counter() - t0
            logger.exception("%s API 失败 | 耗时 %.2fs", prefix, elapsed)
            return '{"has_anomaly": false, "anomaly_reason": "", "stage": "unknown", "progress": ""}'

    async def probe_server_connectivity(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        round_id: int | None = None,
    ) -> str:
        """区服槽状态一眼判断（进入游戏屏 + empty/ready/error）。"""
        prompt = f"""
You judge server/zone selector health on a mobile game pre-entry screen (before in-game HUD).
Use screenshot + OCR. Ignore top-left network speed overlay (GameTurbo).

OCR:
{ocr_summary}

Return JSON only (no markdown fence):
{{
  "on_enter_game_screen": bool,
  "enter_button_visible": bool,
  "server_slot_status": "empty | loading | ready | error | not_visible",
  "server_list_likely_available": bool,
  "has_network_error_ui": bool,
  "confidence": 0.0-1.0,
  "reason": "one sentence",
  "recommendation": "tap_verify | fail_fast | wrong_stage"
}}

Rules:
- on_enter_game_screen=true when main CTA like 踏入仙途/开始游戏/进入游戏/Enter/Start is visible WITH server pick UI above it.
- wrong_stage when still on login, sub-account picker only, or download — NO enter-game CTA.
- server_slot_status=empty: server area visible but no valid server name (blank, dashes ----, only click-to-select hint). empty is NOT healthy — list may be unreachable.
- server_slot_status=error OR has_network_error_ui=true: explicit network/server fetch failure OR toast like 默认服不存在/所选服不存在/请重新选服/server does not exist/re-select server.
- server_slot_status=ready: readable server/zone name in server slot (not dashes).
- If ----- or Click to select Server appears WITH any server-error toast → error + has_network_error_ui=true + fail_fast.
- fail_fast when error UI/toast is visible; tap_verify only when empty slot looks interactive and no error toast.
"""
        prefix = f"[VisionWorker:server_probe] 第 {round_id} 轮" if round_id is not None else "[VisionWorker:server_probe]"
        t0 = time.perf_counter()
        try:
            result = await self._agent.run(
                [prompt, BinaryImage.from_path(screenshot_path)],
            )
            output = (result.output or "").strip()
            logger.info(
                "%s 完成 %.2fs | %s",
                prefix,
                time.perf_counter() - t0,
                output.replace("\n", " ")[:200],
            )
            return output
        except Exception:
            logger.exception("%s API 失败", prefix)
            return "{}"

    async def judge_in_game_main(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        ocr_creation_hits: list[str] | None = None,
        round_id: int | None = None,
        session_index: int = 1,
        sessions_restarted: int = 0,
    ) -> GameEntryJudgment:
        """
        独立判定：是否已进入游戏内（登录完成、局内场景；含强制新手引导蒙层也算进入）。
        创角相关 OCR 命中时必须在 blockers 含 character_creation。
        """
        creation_block = ""
        if ocr_creation_hits:
            creation_block = (
                "\n[Hard rule] OCR hit character-creation keywords: "
                + ", ".join(ocr_creation_hits)
                + ". Unless the screen clearly left creation flow, in_game_main must be false "
                "and blockers must include character_creation.\n"
            )

        session_block = ""
        if sessions_restarted > 0:
            session_block = (
                f"\n[Session] Game session #{session_index}; {sessions_restarted} prior crash/restart(s). "
                "Judge only this screenshot; after restart stage is often resource_download or login.\n"
            )

        prompt = f"""
You judge whether the player reached an in-game playable scene (game automation observer). Use screenshot + OCR only.

OCR (x,y text confidence):
{ocr_summary}
{creation_block}{session_block}

## in_game_main=true when
- Past login/register/server/terms out-of-game UI;
- Past download progress screen;
- Past pure loading/connecting transitions;
- Past character creation (class/name/avatar);
- In-game 3D/2D or HUD; **forced tutorial overlay, mask, finger hint, dialog still count as in-game** (stage may be tutorial_overlay, in_game_main true).

## in_game_main=false when
- Still on login/server pick/download/creation/launcher/desktop;
- Creation OCR hits and UI still looks like creation.

## stage
login | server_select | resource_download | loading | character_creation | tutorial_overlay | in_game_main | unknown

JSON only (no markdown fence):
{{
    "in_game_main": bool,
    "confidence": 0.0-1.0,
    "stage": "enum above",
    "ocr_signals": ["key OCR snippets you used"],
    "reason": "one sentence",
    "blockers": ["e.g. character_creation, login_screen, or []"]
}}
"""
        prefix = (
            f"[VisionWorker:game_entry] 第 {round_id} 轮"
            if round_id is not None
            else "[VisionWorker:game_entry]"
        )
        model = self._llm_config.model_name
        logger.info(
            "%s 进入游戏判定 | model=%s | 截图=%s",
            prefix,
            model,
            screenshot_path.name,
        )
        t0 = time.perf_counter()
        try:
            result = await self._agent.run([prompt, BinaryImage.from_path(screenshot_path)])
            raw = (result.output or "").strip()
            elapsed = time.perf_counter() - t0
            judgment = _parse_game_entry_judgment(raw)
            logger.info(
                "%s 判定 | %.2fs | in_game=%s conf=%.2f stage=%s | %s",
                prefix,
                elapsed,
                judgment.in_game_main,
                judgment.confidence,
                judgment.stage,
                judgment.reason[:200],
            )
            if ocr_creation_hits and judgment.in_game_main:
                judgment = judgment.model_copy(
                    update={
                        "in_game_main": False,
                        "blockers": list(
                            dict.fromkeys(
                                [*judgment.blockers, "character_creation"],
                            ),
                        ),
                        "reason": (
                            f"OCR creation override: {ocr_creation_hits}; "
                            + judgment.reason
                        )[:500],
                    },
                )
            return judgment
        except Exception:
            elapsed = time.perf_counter() - t0
            logger.exception("%s API 失败 | %.2fs", prefix, elapsed)
            return GameEntryJudgment(
                in_game_main=False,
                confidence=0.0,
                stage="unknown",
                reason="Multimodal API call failed",
            )


def _parse_game_entry_judgment(raw: str) -> GameEntryJudgment:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return GameEntryJudgment.model_validate(json.loads(text))
    except Exception:
        return GameEntryJudgment(
            in_game_main=False,
            confidence=0.0,
            stage="unknown",
            reason=f"Failed to parse model JSON: {text[:300]}",
        )
