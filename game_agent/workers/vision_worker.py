from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryImage

from game_agent.models.game_entry_judgment import GameEntryJudgment
from game_agent.models.checkbox_tap_alignment import CheckboxTapAlignmentJudgment
from game_agent.models.privacy_checkbox_judgment import PrivacyCheckboxJudgment
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
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = time.perf_counter() - t0
            err_name = type(e).__name__
            if err_name == "ClosedResourceError":
                logger.warning(
                    "%s 已取消（图结束或任务被替换）| 耗时 %.2fs",
                    prefix,
                    elapsed,
                )
            else:
                logger.exception("%s API 失败 | 耗时 %.2fs", prefix, elapsed)
            return '{"has_anomaly": false, "anomaly_reason": "", "stage": "unknown", "progress": ""}'

    async def interpret_launch_screen(
        self,
        *,
        screenshot_path: Path,
        prompt: str,
        round_id: int | None = None,
    ) -> str:
        """Launch ScreenInterpreter：返回 stage/blocking/tap_target JSON。"""
        prefix = f"[VisionWorker:interpret] 第 {round_id} 轮" if round_id is not None else "[VisionWorker:interpret]"
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
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.exception("%s API 失败 | 耗时 %.2fs", prefix, elapsed)
            return json.dumps(
                {
                    "stage": "unknown",
                    "blocking": False,
                    "tap_target": None,
                    "completion_signals": [],
                    "reason": str(e)[:200],
                },
                ensure_ascii=False,
            )

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
  "blocking_overlay": bool,
  "dismiss_tap_x": int,
  "dismiss_tap_y": int,
  "confidence": 0.0-1.0,
  "reason": "one sentence",
  "recommendation": "tap_verify | fail_fast | wrong_stage | dismiss_overlay"
}}

Rules:
- on_enter_game_screen=true when main CTA like 踏入仙途/开始游戏/进入游戏/Enter/Start is visible WITH server pick UI above it, AND no foreground login/sub-account panel blocking the screen.
- wrong_stage when still on login, sub-account picker, or download.
- wrong_stage when right-side overlay shows Sub-account / Last login / Create Sub-account / Purchase Sub-account / 小号 / 子账号 — even if background enter-game CTA (踏入仙途) is visible behind the panel.
- Foreground login/sub-account panel always wins over background enter-game or server-slot OCR.
- blocking_overlay=true when Notice/公告/活动/日常通知 popup covers server slot or blocks interaction; set recommendation=dismiss_overlay.
- dismiss_tap_x/y: close button coords, or blank area outside the panel (NOT on Start Game / enter CTA). Use device touch pixels matching OCR.
- server_slot_status=empty: server area visible but no valid server name (blank, dashes ----, only click-to-select hint). empty is NOT healthy — list may be unreachable.
- server_slot_status=error OR has_network_error_ui=true: explicit network/server fetch failure OR toast like 默认服不存在/所选服不存在/请重新选服/server does not exist/re-select server.
- server_slot_status=ready: readable server/zone name in server slot (not dashes).
- If ----- or Click to select Server appears WITH any server-error toast → error + has_network_error_ui=true + fail_fast.
- fail_fast when error UI/toast is visible; tap_verify only when no blocking overlay and empty slot looks interactive.
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

    async def probe_server_panel_opened(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str = "",
        round_id: int | None = None,
    ) -> str:
        """点击区服后：判断同屏区服列表弹窗是否已打开。"""
        ocr_block = f"\nOCR (may be incomplete):\n{ocr_summary}\n" if ocr_summary else ""
        prompt = f"""
You verify whether a server/zone LIST PANEL opened as a same-screen overlay after tapping the server slot.
Use the screenshot{ocr_block}

Return JSON only (no markdown fence):
{{
  "server_list_panel_open": bool,
  "same_screen_enter_cta": bool,
  "confidence": 0.0-1.0,
  "reason": "one sentence"
}}

Rules:
- server_list_panel_open=true when a modal/panel shows server list or zone picker (titles like 选择区服/选择服务器/Select Server/Server List, tabs like 最近登录/推荐, server rows, status legend 火爆/流畅/维护).
- same_screen_enter_cta=true when Start Game / 开始游戏 / 踏入仙途 / Enter is STILL visible behind or below the panel (dimmed background), NOT a full page navigation to login or sub-account screen.
- server_list_panel_open=false for: no visible change, only OCR junk/single chars, login page, sub-account picker, Notice/公告 blocking overlay without server list, or resource download screen.
- Ignore top-left GameTurbo network overlay (GT[HK], ms, Mbps).
- Accept equivalent titles: 选择区服 = 选择服务器 = Select Server.
"""
        prefix = (
            f"[VisionWorker:server_panel] 第 {round_id} 轮"
            if round_id is not None
            else "[VisionWorker:server_panel]"
        )
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

    async def judge_privacy_checkbox_state(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        candidate_cx: int | None = None,
        candidate_cy: int | None = None,
        roi_box: tuple[int, int, int, int] | None = None,
        before_screenshot_path: Path | None = None,
        round_id: int | None = None,
    ) -> PrivacyCheckboxJudgment:
        """
        判定协议 checkbox 是否已勾选。
        单图模式：判断当前 state。
        双图模式（before + after）：判断 after 相对 before 是否进入 checked。
        """
        roi_hint = ""
        if roi_box is not None:
            x1, y1, x2, y2 = roi_box
            roi_hint = (
                f"\nCandidate checkbox ROI (device pixels): x1={x1}, y1={y1}, x2={x2}, y2={y2}."
            )
        tap_hint = ""
        if candidate_cx is not None and candidate_cy is not None:
            tap_hint = f"\nOCR-estimated tap point: ({candidate_cx}, {candidate_cy})."

        compare_block = ""
        if before_screenshot_path is not None:
            compare_block = (
                "\nYou receive TWO images: first=before tap, second=after tap. "
                "Judge whether the privacy/terms checkbox became checked after the tap. "
                "Return state=checked if after shows selected (checkmark, filled box, highlight, circle tick). "
                "Return state=unchecked if still clearly empty. "
                "Return state=uncertain if cannot tell.\n"
            )
        else:
            compare_block = (
                "\nJudge the CURRENT checkbox state on this single screenshot. "
                "Do NOT assume it was just tapped.\n"
            )

        prompt = f"""
You judge a mobile game privacy/terms agreement checkbox near text like
"已阅读并同意", "I have read and agree", "用户协议", "隐私政策".
Ignore top-left GameTurbo network overlay.
{compare_block}
OCR:
{ocr_summary}
{tap_hint}{roi_hint}

Return JSON only (no markdown fence):
{{
  "state": "checked | unchecked | not_found | uncertain",
  "confidence": 0.0-1.0,
  "checkbox_visible": bool,
  "reason": "one sentence"
}}

Rules:
- checked: visible checkmark, filled/highlighted box, tick inside square/circle, or clearly selected.
- unchecked: empty square/circle clearly visible and NOT selected.
- not_found: no privacy checkbox near terms text on screen.
- uncertain: checkbox area occluded, too small, or ambiguous.
- checkbox_visible=true when you can see the control even if state is uncertain.
"""
        prefix = (
            f"[VisionWorker:checkbox] 第 {round_id} 轮"
            if round_id is not None
            else "[VisionWorker:checkbox]"
        )
        images: list = [prompt]
        if before_screenshot_path is not None:
            images.append(BinaryImage.from_path(before_screenshot_path))
        images.append(BinaryImage.from_path(screenshot_path))

        t0 = time.perf_counter()
        try:
            result = await self._agent.run(images)
            raw = (result.output or "").strip()
            judgment = parse_privacy_checkbox_judgment(raw)
            logger.info(
                "%s 判定 | %.2fs | state=%s conf=%.2f visible=%s | %s",
                prefix,
                time.perf_counter() - t0,
                judgment.state,
                judgment.confidence,
                judgment.checkbox_visible,
                judgment.reason[:200],
            )
            return judgment
        except Exception:
            logger.exception("%s API 失败 | %.2fs", prefix, time.perf_counter() - t0)
            return PrivacyCheckboxJudgment(
                state="uncertain",
                confidence=0.0,
                checkbox_visible=False,
                reason="Multimodal API call failed",
            )


    async def judge_checkbox_tap_alignment(
        self,
        *,
        screenshot_path: Path,
        tap_x: int,
        tap_y: int,
        ocr_summary: str = "",
        round_id: int | None = None,
    ) -> CheckboxTapAlignmentJudgment:
        """
        判断标注了红点/十字的 tap 是否落在协议 checkbox 上（而非协议文字）。
        用于离线 debug 图与 OCR 左推坐标的真实对齐验证。
        """
        prompt = f"""
You verify whether a proposed tap point hits the privacy/terms CHECKBOX control.

The screenshot may show a RED dot with YELLOW crosshair marking the proposed tap at
approximately ({tap_x}, {tap_y}) in device logical pixels (origin top-left).

OCR near the terms line:
{ocr_summary[:2000]}

The checkbox is a small square/circle to the LEFT of text like
"我已阅读", "已阅读并同意", "I have read and agree", NOT on the colored link text.

Return JSON only (no markdown fence):
{{
  "on_checkbox": bool,
  "confidence": 0.0-1.0,
  "reason": "one sentence",
  "adjust_direction": "left | right | up | down | ok"
}}

Rules:
- on_checkbox=true ONLY if the red marker center is on/over the checkbox control box.
- on_checkbox=false if marker is on the agreement TEXT (e.g. on 我/阅/协议 chars) or empty background too far from checkbox.
- adjust_direction=left if marker should move left to reach checkbox; right/up/down similarly; ok if aligned.
"""
        prefix = (
            f"[VisionWorker:checkbox_align] 第 {round_id} 轮"
            if round_id is not None
            else "[VisionWorker:checkbox_align]"
        )
        t0 = time.perf_counter()
        try:
            result = await self._agent.run(
                [prompt, BinaryImage.from_path(screenshot_path)],
            )
            raw = (result.output or "").strip()
            judgment = parse_checkbox_tap_alignment(raw)
            logger.info(
                "%s | %.2fs | on_checkbox=%s conf=%.2f dir=%s | %s",
                prefix,
                time.perf_counter() - t0,
                judgment.on_checkbox,
                judgment.confidence,
                judgment.adjust_direction,
                judgment.reason[:200],
            )
            return judgment
        except Exception:
            logger.exception("%s API 失败", prefix)
            return CheckboxTapAlignmentJudgment(
                on_checkbox=False,
                confidence=0.0,
                reason="Multimodal API call failed",
                adjust_direction="ok",
            )


def parse_checkbox_tap_alignment(raw: str) -> CheckboxTapAlignmentJudgment:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        data = json.loads(text)
        direction = str(data.get("adjust_direction", "ok")).strip().lower()
        if direction not in ("left", "right", "up", "down", "ok"):
            direction = "ok"
        return CheckboxTapAlignmentJudgment(
            on_checkbox=bool(data.get("on_checkbox", False)),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            reason=str(data.get("reason", "") or "")[:500],
            adjust_direction=direction,
        )
    except Exception:
        return CheckboxTapAlignmentJudgment(
            on_checkbox=False,
            confidence=0.0,
            reason=f"Failed to parse model JSON: {text[:300]}",
            adjust_direction="ok",
        )


def parse_privacy_checkbox_judgment(raw: str) -> PrivacyCheckboxJudgment:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        data = json.loads(text)
        state = str(data.get("state", "uncertain")).strip().lower()
        if state not in ("checked", "unchecked", "not_found", "uncertain"):
            state = "uncertain"
        return PrivacyCheckboxJudgment(
            state=state,
            confidence=float(data.get("confidence", 0.0) or 0.0),
            checkbox_visible=bool(data.get("checkbox_visible", False)),
            reason=str(data.get("reason", "") or "")[:500],
        )
    except Exception:
        return PrivacyCheckboxJudgment(
            state="uncertain",
            confidence=0.0,
            checkbox_visible=False,
            reason=f"Failed to parse model JSON: {text[:300]}",
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
