from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryImage

from game_agent.models.download_gate import DownloadGateJudgment
from game_agent.models.game_entry_judgment import GameEntryJudgment
from game_agent.models.in_game_screen_analysis import InGameScreenAnalysis
from game_agent.models.checkbox_tap_alignment import CheckboxTapAlignmentJudgment
from game_agent.models.in_game_progress import InGameSessionProgressJudgment
from game_agent.models.tutorial_pulse import TutorialPulsePick
from game_agent.models.privacy_checkbox_judgment import PrivacyCheckboxJudgment
from game_agent.models.privacy_gate import PrivacyGateJudgment
from game_agent.models.sub_account_gate import SubAccountGateJudgment
from game_agent.models.scene_gate import SceneGateJudgment
from game_agent.models.settings import LLMSection
from game_agent.services.llm_service import build_llm_model
from game_agent.utils.vision_log import log_full_text, log_vision_json

logger = logging.getLogger(__name__)


class VisionWorker:
    """Multimodal observer: analyze and report; no direct device actions."""

    def __init__(
        self,
        llm_config: LLMSection,
        *,
        attempt_context: object | None = None,
    ) -> None:
        self._llm_config = llm_config
        self._agent = Agent(build_llm_model(llm_config), output_type=str)
        self._attempt_context = attempt_context

    def _capture_generation(self) -> int:
        if self._attempt_context is not None:
            return self._attempt_context.get_session_generation()  # type: ignore[union-attr]
        from game_agent.modules.session_invalidation import capture_session_generation

        return capture_session_generation()

    def _is_stale(self, captured: int) -> bool:
        if self._attempt_context is not None:
            return self._attempt_context.is_session_generation_stale(captured)  # type: ignore[union-attr]
        from game_agent.modules.session_invalidation import is_stale_generation

        return is_stale_generation(captured)

    async def _run_agent(
        self,
        inputs,
        *,
        prefix: str = "[VisionWorker]",
        fallback: str = "",
        work_generation: int | None = None,
    ) -> str:
        gen = work_generation if work_generation is not None else self._capture_generation()
        if self._is_stale(gen):
            logger.warning("%s skip(pre) | session stale gen=%d", prefix, gen)
            return fallback
        try:
            result = await self._agent.run(inputs)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s API failed", prefix)
            raise
        if self._is_stale(gen):
            logger.warning("%s drop(post) | session stale gen=%d", prefix, gen)
            return fallback
        return (result.output or "").strip()

    @staticmethod
    def _require_model_output(raw: str, *, prefix: str) -> str:
        text = (raw or "").strip()
        if not text:
            raise RuntimeError(f"{prefix} multimodal API returned no output")
        return text

    async def analyze_game_state(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        round_id: int | None = None,
    ) -> str:
        """
        Monitor game state (download, login, in-game) and anomalies.
        Returns a JSON string with status, stage, message, etc.
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
        prefix = f"[VisionWorker] round {round_id}" if round_id is not None else "[VisionWorker]"
        model = self._llm_config.model_name
        logger.info(
            "%s multimodal API | model=%s | screenshot=%s",
            prefix,
            model,
            screenshot_path.name,
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent([prompt, BinaryImage.from_path(screenshot_path)])
            output = result
            elapsed = time.perf_counter() - t0
            log_vision_json(
                logger,
                prefix,
                output,
                summary=f"API response | {elapsed:.2f}s",
            )
            return output
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = time.perf_counter() - t0
            err_name = type(e).__name__
            if err_name == "ClosedResourceError":
                logger.warning(
                    "%s cancelled (graph ended or task replaced) | %.2fs",
                    prefix,
                    elapsed,
                )
            else:
                logger.exception("%s API failed | %.2fs", prefix, elapsed)
            return '{"has_anomaly": false, "anomaly_reason": "", "stage": "unknown", "progress": ""}'

    async def judge_in_game_stability(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        round_id: int | None = None,
    ) -> str:
        """
        Post-entry stability: network dialogs, asset/model load failures, etc.
        Returns JSON: has_fatal_anomaly, anomaly_reason, stage, loading_ok, reason
        """
        prompt = f"""
You observe an in-game screen after the player entered the game. Judge stability for automation QA.
OCR:
{ocr_summary}

Set has_fatal_anomaly=true when ANY of:
- Network error dialogs/copy (网络连接失败/断开/异常/请检查网络/连接超时/服务器连接失败/资源加载失败/下载失败/更新失败/地区不支持等)
- Stuck on pure black screen with no HUD (likely failed load)
- Explicit resource/model load failure messages
- Infinite loading spinner with error or retry that blocks play

Set has_fatal_anomaly=false for:
- Normal in-game HUD, tutorial overlay, combat, dialogue, finger hints
- Brief loading bars without error text
- Account/login errors (should not appear here)
- Privacy/announcement/event popups without network failure
- Top-left GameTurbo acceleration overlay (GT, Mbps) — ignore it

loading_ok=false only when visible evidence of broken/stuck asset loading (not mere black frame between scenes).

Return valid JSON only (no markdown fence):
{{
    "has_fatal_anomaly": bool,
    "anomaly_reason": "short reason if fatal else empty",
    "loading_ok": bool,
    "stage": "in_game | loading | error_dialog | black_screen | unknown",
    "reason": "one sentence summary"
}}
"""
        prefix = (
            f"[VisionWorker:stability] round {round_id}"
            if round_id is not None
            else "[VisionWorker:stability]"
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent([prompt, BinaryImage.from_path(screenshot_path)])
            output = result
            log_vision_json(
                logger,
                prefix,
                output,
                summary=f"Stability observe | {time.perf_counter() - t0:.2f}s",
            )
            return output
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s API failed", prefix)
            return (
                '{"has_fatal_anomaly": false, "anomaly_reason": "", '
                '"loading_ok": true, "stage": "unknown", "reason": "api_error"}'
            )

    async def plan_phase_spec(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        completed_phases_summary: str = "",
        prior_phase_summary: str = "",
        stall_hint: str = "",
        login_done: bool = False,
        enter_tapped_count: int = 0,
        round_id: int | None = None,
    ) -> str:
        """Plan one PhaseSpec JSON from the screen (generic template, no game enums)."""
        prior_block = ""
        if prior_phase_summary:
            prior_block = f"\nPrior phase (may still be active): {prior_phase_summary}\n"
        stall_block = ""
        if stall_hint:
            stall_block = f"\nStall hint: {stall_hint}\n"
        done_block = ""
        if completed_phases_summary:
            done_block = f"\nCompleted phases this attempt (do NOT repeat these phase_id):\n{completed_phases_summary}\n"

        prompt = f"""
You plan ONE automation step for a mobile game AFTER login, before confirmed in-game.
Use screenshot + OCR. Output JSON for a generic phase template (no game-specific code paths).
Output exactly ONE step per response — never a multi-step plan.

OCR (x,y text):
{ocr_summary}
{done_block}{prior_block}{stall_block}
Context: login_done={login_done}, enter_tapped_count={enter_tapped_count}

If there is NO variable post-login UI (no creation, no class pick, no tutorial gate, already loading/in-game CTA), set flow_active=false.

Do NOT reuse any phase_id listed under Completed phases. Pick a new slug for the next distinct step.

Otherwise set flow_active=true and fill:
- phase_id: short slug (english, e.g. list_pick, confirm_next, dismiss_popup)
- phase_label: short English label (e.g. class_select, dismiss_popup)
- action: tap_xy | wait | press_back | dismiss_blank | none
- x,y: tap coordinates (device space); 0 if not tapping
- wait_s: 1.5-4.0 if action=wait else optional after tap
- target_text: button/list label if any
- reason: one sentence
- complete: {{ "kind": "fingerprint_change|ocr_contains|always_after_wait|manual_next_plan", "hint": "" }}
- confidence: 0.0-1.0

Patterns (abstract):
- If UI says tap blank area to close (e.g. 点击空白处关闭), use action=dismiss_blank with x=0 y=0 (engine runs dismiss tool)
- Vertical list pick then animation then forward button → first phase tap list item + wait; later phase tap forward/下一步/Next
- Character slot then enter world → tap slot then enter CTA (may be handled elsewhere; still plan if you see it)
- If screen is loading only, use action=wait with complete.kind=always_after_wait
- If unsafe to tap, use wait or none with low confidence

JSON only (no markdown fence):
{{
  "flow_active": bool,
  "phase_id": "slug",
  "phase_label": "label",
  "action": "tap_xy|wait|press_back|dismiss_blank|none",
  "x": 0,
  "y": 0,
  "wait_s": 2.0,
  "target_text": "",
  "reason": "",
  "complete": {{ "kind": "fingerprint_change", "hint": "" }},
  "confidence": 0.0
}}
"""
        prefix = (
            f"[VisionWorker:adaptive] round {round_id}"
            if round_id is not None
            else "[VisionWorker:adaptive]"
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent(
                [prompt, BinaryImage.from_path(screenshot_path)],
            )
            output = result
            log_vision_json(
                logger,
                prefix,
                output,
                summary=f"Phase plan | {time.perf_counter() - t0:.2f}s",
            )
            return output
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s API failed", prefix)
            return '{"flow_active": false, "phase_id": "skip", "action": "none", "confidence": 0}'

    async def plan_free_step(
        self,
        *,
        screenshot_path: Path,
        prompt: str,
        round_id: int | None = None,
    ) -> str:
        """Free node: plan one action JSON from screenshot + OCR."""
        prefix = (
            f"[VisionWorker:free] round {round_id}"
            if round_id is not None
            else "[VisionWorker:free]"
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent(
                [prompt, BinaryImage.from_path(screenshot_path)],
            )
            output = result
            log_full_text(
                logger,
                prefix,
                f"done {time.perf_counter() - t0:.2f}s\n{output}",
            )
            return output
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s API failed", prefix)
            return "{}"

    async def interpret_launch_screen(
        self,
        *,
        screenshot_path: Path,
        prompt: str,
        round_id: int | None = None,
    ) -> str:
        """Launch ScreenInterpreter: stage/blocking/tap_target JSON."""
        prefix = f"[VisionWorker:interpret] round {round_id}" if round_id is not None else "[VisionWorker:interpret]"
        model = self._llm_config.model_name
        logger.info(
            "%s multimodal API | model=%s | screenshot=%s",
            prefix,
            model,
            screenshot_path.name,
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent([prompt, BinaryImage.from_path(screenshot_path)])
            output = result
            elapsed = time.perf_counter() - t0
            log_vision_json(
                logger,
                prefix,
                output,
                summary=f"API response | {elapsed:.2f}s",
            )
            return output
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.exception("%s API failed | %.2fs", prefix, elapsed)
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
        """Server slot health (enter-game screen + empty/ready/error)."""
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
        prefix = f"[VisionWorker:server_probe] round {round_id}" if round_id is not None else "[VisionWorker:server_probe]"
        t0 = time.perf_counter()
        try:
            result = await self._run_agent(
                [prompt, BinaryImage.from_path(screenshot_path)],
            )
            output = result
            log_full_text(
                logger,
                prefix,
                f"done {time.perf_counter() - t0:.2f}s\n{output}",
            )
            return output
        except Exception:
            logger.exception("%s API failed", prefix)
            return "{}"

    async def probe_server_panel_opened(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str = "",
        round_id: int | None = None,
    ) -> str:
        """After server tap: whether same-screen server list panel opened."""
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
            f"[VisionWorker:server_panel] round {round_id}"
            if round_id is not None
            else "[VisionWorker:server_panel]"
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent(
                [prompt, BinaryImage.from_path(screenshot_path)],
            )
            output = result
            log_full_text(
                logger,
                prefix,
                f"done {time.perf_counter() - t0:.2f}s\n{output}",
            )
            return output
        except Exception:
            logger.exception("%s API failed", prefix)
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
        Judge whether player reached in-game (login done; forced tutorial overlay counts).
        OCR creation hits require blockers to include character_creation.
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
            f"[VisionWorker:game_entry] round {round_id}"
            if round_id is not None
            else "[VisionWorker:game_entry]"
        )
        model = self._llm_config.model_name
        logger.info(
            "%s game entry | model=%s | screenshot=%s",
            prefix,
            model,
            screenshot_path.name,
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent([prompt, BinaryImage.from_path(screenshot_path)])
            raw = self._require_model_output(result, prefix=prefix)
            elapsed = time.perf_counter() - t0
            judgment = _parse_game_entry_judgment(raw)
            logger.info(
                "%s judgment | %.2fs | in_game=%s conf=%.2f stage=%s",
                prefix,
                elapsed,
                judgment.in_game_main,
                judgment.confidence,
                judgment.stage,
            )
            log_vision_json(
                logger,
                prefix,
                judgment.model_dump(),
                summary="game_entry judgment full",
            )
            log_full_text(logger, prefix, raw)
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
                        ),
                    },
                )
            return judgment
        except Exception:
            elapsed = time.perf_counter() - t0
            logger.exception("%s API failed | %.2fs", prefix, elapsed)
            return GameEntryJudgment(
                in_game_main=False,
                confidence=0.0,
                stage="unknown",
                reason="Multimodal API call failed",
            )

    async def analyze_in_game_screen(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        ocr_candidates_json: str = "",
        motion_summary: str = "",
        spatial_hints: str = "",
        annotated_path: Path | None = None,
        round_id: int | None = None,
    ) -> InGameScreenAnalysis:
        """In-game screen analysis + motion/OCR tap suggestion (no session verdict)."""
        motion_block = ""
        if motion_summary.strip():
            motion_block = f"""
Motion probe (OpenCV temporal diff / pulsing hotspots P1..Pn):
{motion_summary.strip()}
"""
        spatial_block = ""
        if spatial_hints.strip():
            spatial_block = f"""
Spatial fuse candidates (pulse ↔ nearest OCR, for cross-check only):
{spatial_hints.strip()}
"""
        candidates_block = ocr_candidates_json.strip() or "(OCR candidates unavailable)"

        annotated_note = ""
        if annotated_path is not None and annotated_path.is_file():
            annotated_note = (
                "\nA second image shows the same frame with green P1..Pn boxes "
                "(pulsing_fixed hotspots) and orange M boxes (moving sprites).\n"
            )

        prompt = f"""
Analyze the in-game mobile game screen. Player is already past login.
You fuse OpenCV motion hotspots with OCR bounding boxes to recommend ONE tap target.
{annotated_note}
OCR summary (x,y text):
{ocr_summary}

OCR candidates (JSON: text + cx,cy,bbox):
{candidates_block}
{motion_block}{spatial_block}

Workflow:
1. Describe the scene (tutorial modal, technique cards, dialog, HUD, loading).
2. For each pulsing hotspot P1..Pn: what UI element is it on? Tutorial highlight or noise
   (progress bar, HUD timer, empty gap)?
3. Match hotspots to OCR rows: which pulse aligns with a clickable card/button/dialog area?
4. Recommend exactly ONE tap (or wait/swipe if truly needed).

Rules:
- Prefer tap_text anchored to an OCR candidate row; coordinates must match that row's cx,cy.
- Use motion_ocr_fused only when a pulse clearly overlaps a tutorial highlight ON an OCR element.
- When tutorial text says click a card/icon with NO OCR text on that target (e.g. 点击卡牌),
  set recommended_action=tap_xy, tap_source=motion_pulse, tap_x=0, tap_y=0 (OpenCV will inject
  precise pulse coords later). Do NOT invent pixel coordinates.
- For dialog with "Click Blank to Continue" / 点击空白: tap_source=dialogue_blank, tap the
  dialogue blank area (NOT the CTA text bbox); set tap_x/tap_y to lower-center safe zone.
- screen_static=false if ONLY dialogue text or card descriptions changed (HUD may be unchanged).
- Reject progress-bar / HUD-number pulses in rejected_pulses.
- If instructional blank-continue text is visible (Click Blank to Continue / 点击空白继续),
  set use_dim_region_tap=true and tap_source=dialogue_blank; do NOT tap that text bbox.
- Do NOT output success/fail/verdict for the whole session.
- semantic_target_text: use FULL OCR button label (e.g. 战斗 not 战) when target has readable text.
- target_has_ocr_semantics: true when the tap target has its own OCR row (button/card name).
- recommended_coord_source: ocr if OCR semantics exist; pulse if no OCR on target but pulse/glow;
  vlm_xy if motion_ocr_fused with reliable tap_x/y; dialogue_blank for blank-continue dialogs.
- Never use a single-character tap_target_text when a longer OCR row exists on the same button.

JSON only (no markdown fence):
{{
    "forced_guidance_present": bool,
    "guidance_signals": ["tag"],
    "ui_stage": "tutorial | combat | dialog | loading | hud | unknown",
    "screen_static": bool,
    "loading_or_blocking": bool,
    "progress_observation": "one sentence",
    "observations": "2-4 sentences",
    "analysis": "short summary",
    "confidence": 0.0-1.0,
    "recommended_action": "tap_xy | tap_text | swipe | wait | none",
    "tap_target_text": "exact OCR text or empty",
    "tap_x": 0,
    "tap_y": 0,
    "tap_x2": 0,
    "tap_y2": 0,
    "tap_source": "none | ocr_bbox | motion_ocr_fused | motion_pulse | dialogue_blank",
    "fusion_reason": "why this tap",
    "rejected_pulses": ["P4: progress bar noise"],
    "tap_confidence": 0.0-1.0,
    "use_dim_region_tap": false,
    "dim_region_hint": "",
    "target_has_ocr_semantics": false,
    "semantic_target_text": "",
    "recommended_coord_source": "none | ocr | pulse | vlm_xy | dialogue_blank"
}}
"""
        prefix = (
            f"[VisionWorker:in_game_analyze] round {round_id}"
            if round_id is not None
            else "[VisionWorker:in_game_analyze]"
        )
        model = self._llm_config.model_name
        logger.info(
            "%s in-game fusion | model=%s | screenshot=%s annotated=%s",
            prefix,
            model,
            screenshot_path.name,
            annotated_path.name if annotated_path else "none",
        )
        t0 = time.perf_counter()
        try:
            images: list = [prompt, BinaryImage.from_path(screenshot_path)]
            if annotated_path is not None and annotated_path.is_file():
                images.append(BinaryImage.from_path(annotated_path))
            result = await self._run_agent(images)
            elapsed = time.perf_counter() - t0
            analysis = _parse_in_game_screen_analysis(
                self._require_model_output(result, prefix=prefix),
            )
            logger.info(
                "%s analysis | %.2fs | stage=%s guidance=%s tap=%s@(%d,%d) conf=%.2f",
                prefix,
                elapsed,
                analysis.ui_stage,
                analysis.forced_guidance_present,
                analysis.recommended_action,
                analysis.tap_x,
                analysis.tap_y,
                analysis.tap_confidence,
            )
            log_vision_json(
                logger,
                prefix,
                analysis.model_dump(),
                summary="in_game_screen_analysis full",
            )
            log_full_text(logger, prefix, result)
            return analysis
        except Exception:
            elapsed = time.perf_counter() - t0
            logger.exception("%s API failed | %.2fs", prefix, elapsed)
            return InGameScreenAnalysis(
                confidence=0.0,
                observations="Multimodal API call failed",
                analysis="analysis unavailable",
            )

    async def judge_tutorial_pulse(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        motion_summary: str,
        tutorial_intent: str = "",
        annotated_path: Path | None = None,
        round_id: int | None = None,
    ) -> TutorialPulsePick:
        """Pick which OpenCV pulse P1..Pn is the tutorial target (no pixel coords)."""
        annotated_note = ""
        if annotated_path is not None and annotated_path.is_file():
            annotated_note = (
                "\nSecond image: green P1..Pn = pulsing_fixed tutorial hotspots; "
                "orange M = moving sprites (usually noise).\n"
            )
        prompt = f"""
You judge which OpenCV motion pulse is the forced tutorial tap target on a mobile game screen.
Do NOT output tap_x/tap_y — automation uses pulse center coordinates from OpenCV.

Tutorial intent: {tutorial_intent or "click non-text UI (card/icon/glow)"}
{annotated_note}
OCR:
{ocr_summary[:2500]}

Motion probe (pulsing_fixed ranks P1..Pn):
{motion_summary[:2000]}

Tasks:
1. Is forced tutorial guidance visible (finger, glow ring, mask)?
2. Which P rank is on the intended tap target (card, slot, glowing button)?
3. Which P ranks are noise (battlefield idle, progress bar, HUD timer)?

Return JSON only (no markdown fence):
{{
  "forced_guidance_present": bool,
  "chosen_pulse_rank": 0,
  "reject_ranks": [3, 4],
  "preferred_band": "top | middle | lower | ",
  "target_description": "short",
  "confidence": 0.0-1.0,
  "reason": "one sentence"
}}

Rules:
- chosen_pulse_rank is 1-based index matching P1, P2, ... in motion summary; 0 if unsure.
- Prefer pulse on tutorial highlight / finger target, not moving character sprites.
- Game UI may be SC/TC/EN; match semantically not by fixed screen position.
"""
        prefix = (
            f"[VisionWorker:tutorial_pulse] round {round_id}"
            if round_id is not None
            else "[VisionWorker:tutorial_pulse]"
        )
        t0 = time.perf_counter()
        try:
            images: list = [prompt, BinaryImage.from_path(screenshot_path)]
            if annotated_path is not None and annotated_path.is_file():
                images.append(BinaryImage.from_path(annotated_path))
            result = await self._run_agent(images)
            pick = _parse_tutorial_pulse_pick(
                self._require_model_output(result, prefix=prefix),
            )
            logger.info(
                "%s pick | %.2fs | rank=%d conf=%.2f | %s",
                prefix,
                time.perf_counter() - t0,
                pick.chosen_pulse_rank,
                pick.confidence,
                pick.reason[:120],
            )
            return pick
        except Exception:
            logger.exception("%s API failed | %.2fs", prefix, time.perf_counter() - t0)
            return TutorialPulsePick(
                confidence=0.0,
                reason="Multimodal API call failed",
            )

    async def judge_in_game_session_progress(
        self,
        *,
        screenshot_path: Path,
        before_ocr_summary: str,
        after_ocr_summary: str,
        before_analysis_summary: str = "",
        round_id: int | None = None,
    ) -> InGameSessionProgressJudgment:
        """After action: whether in-game tutorial visibly advanced vs prior round."""
        prompt = f"""
You judge whether an in-game mobile game session made VISIBLE progress after the last automation action.
The player is past login; this may be a forced tutorial (finger hint, glowing card, dialogue).

Before action — OCR:
{before_ocr_summary[:2000]}

After action — OCR:
{after_ocr_summary[:2000]}

Before action — prior VLM summary:
{(before_analysis_summary or "(none)")[:800]}

Look at the AFTER screenshot and compare to the before context.

session_progressed=true examples:
- Tutorial overlay dismissed or forced guidance cleared
- Tutorial step advanced (new dialogue, card selected/checked/deployed, new UI panel)
- Entered playable HUD / combat from tutorial
- Clear UI state change beyond loading spinner

session_progressed=false examples:
- Same tutorial prompt and same blocking UI after tap
- Still pointing at the same glowing target with no new state
- Only animation flicker, no new interactive state

If loading/connecting fullscreen with no new interactive UI, set session_progressed=false but confidence low.

Game UI may be SC/TC/EN. Card selection, checkmarks, deploy to field count as progress.

Return JSON only (no markdown fence):
{{
  "session_progressed": true,
  "confidence": 0.0-1.0,
  "reason": "one sentence"
}}
"""
        prefix = (
            f"[VisionWorker:in_game_progress] round {round_id}"
            if round_id is not None
            else "[VisionWorker:in_game_progress]"
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent([prompt, BinaryImage.from_path(screenshot_path)])
            judgment = _parse_in_game_session_progress(
                self._require_model_output(result, prefix=prefix),
            )
            logger.info(
                "%s judgment | %.2fs | progressed=%s conf=%.2f | %s",
                prefix,
                time.perf_counter() - t0,
                judgment.session_progressed,
                judgment.confidence,
                judgment.reason[:120],
            )
            return judgment
        except Exception:
            logger.exception("%s API failed | %.2fs", prefix, time.perf_counter() - t0)
            return InGameSessionProgressJudgment(
                session_progressed=False,
                confidence=0.0,
                reason="Multimodal API call failed",
            )

    async def judge_privacy_gate(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        round_id: int | None = None,
    ) -> PrivacyGateJudgment:
        """Pre-route: cold-start privacy modal vs login-page agreement checkbox."""
        prompt = f"""
You classify which privacy UI blocks the game launch on this mobile screenshot.

Two distinct patterns:
1) modal — full-screen or blocking dialog with body text about privacy/terms AND bottom
   action buttons like 不同意 + 同意并进入 / Agree and Enter. No login checkbox row.
2) checkbox — login/register screen with a small checkbox LEFT of inline text like
   "已阅读并同意" / "I have read and agree" before tapping 开始游戏 / Login.
3) none — no blocking privacy UI (loading, in-game, login without privacy gate, etc.).

Ignore top-left GameTurbo network overlay.

OCR:
{ocr_summary[:3000]}

Return JSON only (no markdown fence):
{{
  "gate_kind": "modal | checkbox | none",
  "confidence": 0.0-1.0,
  "tap_x": 0,
  "tap_y": 0,
  "tap_label": "affirmative button label if modal",
  "reason": "one sentence"
}}

Rules:
- modal: prefer when 不同意 and 同意并进入 (or similar pair) appear as buttons, even if OCR
  also mentions 已阅读并同意 in the dialog body.
- checkbox: small control beside terms line on a login form, not a full-screen consent dialog.
- none: privacy already accepted or screen is unrelated.
- For gate_kind=modal, set tap_x/tap_y to the affirmative/consent button center (device pixels).
- For checkbox or none, tap_x/tap_y=0.
"""
        prefix = (
            f"[VisionWorker:privacy_gate] round {round_id}"
            if round_id is not None
            else "[VisionWorker:privacy_gate]"
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent([prompt, BinaryImage.from_path(screenshot_path)])
            raw = self._require_model_output(result, prefix=prefix)
            judgment = parse_privacy_gate_judgment(raw)
            logger.info(
                "%s judgment | %.2fs | gate=%s conf=%.2f",
                prefix,
                time.perf_counter() - t0,
                judgment.gate_kind,
                judgment.confidence,
            )
            log_vision_json(logger, prefix, judgment.model_dump(), summary="privacy_gate full")
            return judgment
        except Exception:
            logger.exception("%s API failed | %.2fs", prefix, time.perf_counter() - t0)
            return PrivacyGateJudgment(
                gate_kind="unknown",
                confidence=0.0,
                reason="Multimodal API call failed",
            )

    async def judge_download_gate(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        round_id: int | None = None,
    ) -> DownloadGateJudgment:
        """Resource download screen: progress, wait vs continue, continue button coords."""
        prompt = f"""
You classify whether this mobile game screen shows a resource/asset download or update UI.

OCR:
{ocr_summary[:3000]}

Return JSON only (no markdown fence):
{{
  "is_download": true,
  "in_progress": true,
  "progress_text": "35%",
  "action": "wait | tap_continue | done",
  "tap_x": 0,
  "tap_y": 0,
  "confidence": 0.0-1.0,
  "reason": "one sentence"
}}

Rules:
- is_download=true for download/update/patch screens with progress bar or MB/GB text.
- in_progress=true while downloading; false when complete.
- action=wait while downloading; tap_continue if a Continue/确定/确认 button is visible;
  done when download UI is gone.
- For tap_continue, set tap_x/tap_y to button center (device pixels).
- Ignore top-left GameTurbo network overlay.
"""
        prefix = (
            f"[VisionWorker:download_gate] round {round_id}"
            if round_id is not None
            else "[VisionWorker:download_gate]"
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent([prompt, BinaryImage.from_path(screenshot_path)])
            raw = self._require_model_output(result, prefix=prefix)
            judgment = parse_download_gate_judgment(raw)
            logger.info(
                "%s judgment | %.2fs | download=%s action=%s",
                prefix,
                time.perf_counter() - t0,
                judgment.is_download,
                judgment.action,
            )
            log_vision_json(logger, prefix, judgment.model_dump(), summary="download_gate full")
            return judgment
        except Exception:
            logger.exception("%s API failed | %.2fs", prefix, time.perf_counter() - t0)
            return DownloadGateJudgment(
                is_download=False,
                confidence=0.0,
                reason="Multimodal API call failed",
            )

    async def judge_sub_account_gate(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        round_id: int | None = None,
        target_label: str = "",
    ) -> SubAccountGateJudgment:
        """Sub-account picker: classify screen and locate existing account tap."""
        target_block = ""
        if (target_label or "").strip():
            target_block = f"""
Target sub-account (from credentials): {target_label.strip()}
- Prefer tapping the row matching this label (English match is case-insensitive).
- Do NOT tap description/help/create/purchase buttons.
"""
        prompt = f"""
You classify whether this mobile game screen is a sub-account / alt-account selection page.
{target_block}
OCR:
{ocr_summary[:3000]}

Return JSON only (no markdown fence):
{{
  "is_sub_account": true,
  "confidence": 0.0-1.0,
  "tap_x": 0,
  "tap_y": 0,
  "tap_label": "existing account label",
  "reason": "one sentence"
}}

Rules:
- is_sub_account=true when user must pick among existing accounts / last login entries.
- Tap ONLY an existing account row or "last login" entry — NOT create/purchase sub-account buttons.
- Set tap_x/tap_y to the center of the chosen existing account row (device pixels).
- Ignore top-left GameTurbo network overlay.
"""
        prefix = (
            f"[VisionWorker:sub_account_gate] round {round_id}"
            if round_id is not None
            else "[VisionWorker:sub_account_gate]"
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent([prompt, BinaryImage.from_path(screenshot_path)])
            raw = self._require_model_output(result, prefix=prefix)
            judgment = parse_sub_account_gate_judgment(raw)
            logger.info(
                "%s judgment | %.2fs | sub_account=%s conf=%.2f",
                prefix,
                time.perf_counter() - t0,
                judgment.is_sub_account,
                judgment.confidence,
            )
            log_vision_json(logger, prefix, judgment.model_dump(), summary="sub_account_gate full")
            return judgment
        except Exception:
            logger.exception("%s API failed | %.2fs", prefix, time.perf_counter() - t0)
            return SubAccountGateJudgment(
                is_sub_account=False,
                confidence=0.0,
                reason="Multimodal API call failed",
            )

    async def judge_scene_gate(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        rule_scene_id: str = "unknown",
        rule_confidence: float = 0.0,
        active_strategy: str = "",
        round_id: int | None = None,
        known_labels: list[dict] | None = None,
    ) -> SceneGateJudgment:
        """Describe and classify current game screen; open label_slug, no fixed enum."""
        known_block = ""
        if known_labels:
            lines = []
            for row in known_labels[:20]:
                lid = row.get("label_id", "")
                slug = row.get("label_slug", "")
                strat = row.get("coord_strategy", "")
                target = row.get("semantic_target", "")
                disp = str(row.get("label_display", ""))[:80]
                lines.append(f"- id={lid} slug={slug} strategy={strat} target={target!r} | {disp}")
            known_block = (
                "Known scene labels from prior successful runs (reuse slug/id when same screen):\n"
                + "\n".join(lines)
            )

        prompt = f"""
You observe a mobile game screenshot for test automation. Classify the scene with an open-vocabulary label_slug (snake_case).

OCR (may be incomplete; trust the image when OCR conflicts):
{ocr_summary[:3000]}

Heuristic OCR rule guess: scene={rule_scene_id} confidence={rule_confidence:.2f}
Active automation strategy: {active_strategy or "none"}

{known_block}

Return JSON only (no markdown fence):
{{
  "label_slug": "snake_case e.g. pre_battle_deploy_tutorial_battle_cta",
  "label_display": "short human-readable scene name",
  "confidence": 0.0-1.0,
  "coord_strategy": "ocr | pulse | dim_region | wait | vlm_semantic | none",
  "semantic_target": "OCR anchor when coord_strategy=ocr e.g. 战斗",
  "match_prior_label_id": "known label id if matches list above else empty",
  "legacy_scene_hint": "dialogue | tutorial | loading | in_game_hud | unknown",
  "description": "one sentence describing visible UI",
  "reason": "why this coord_strategy and target",
  "use_dim_region_tap": false,
  "dim_region_hint": ""
}}

Rules:
- Do NOT output coordinates.
- Glowing finger/highlight on a button → coord_strategy=pulse, semantic_target=button text (e.g. 战斗), NOT dialogue bubble.
- Story speech bubble only → coord_strategy=ocr, legacy_scene_hint=dialogue.
- Blank/dim continue → coord_strategy=dim_region, use_dim_region_tap=true.
- Loading → coord_strategy=wait.

Optional legacy: "scene_id", "action" (tap_dialogue|wait|none)

Ignore top-left GameTurbo overlay.
"""
        prefix = (
            f"[VisionWorker:scene_gate] round {round_id}"
            if round_id is not None
            else "[VisionWorker:scene_gate]"
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent([prompt, BinaryImage.from_path(screenshot_path)])
            raw = self._require_model_output(result, prefix=prefix)
            judgment = parse_scene_gate_judgment(raw)
            logger.info(
                "%s judgment | %.2fs | slug=%s conf=%.2f strategy=%s",
                prefix,
                time.perf_counter() - t0,
                judgment.normalized_slug(),
                judgment.confidence,
                judgment.normalized_coord_strategy(),
            )
            log_vision_json(logger, prefix, judgment.model_dump(), summary="scene_gate full")
            return judgment
        except Exception:
            logger.exception("%s API failed | %.2fs", prefix, time.perf_counter() - t0)
            return SceneGateJudgment(
                scene_id="unknown",
                confidence=0.0,
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
        Judge privacy/terms checkbox checked state.
        Single image: current state. Dual image (before + after): whether after is checked.
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
  "reason": "one sentence",
  "suggested_action": "tap_checkbox | none",
  "tap_x": 0,
  "tap_y": 0,
  "tap_label": ""
}}

Rules:
- checked: visible checkmark, filled/highlighted box, tick inside square/circle, or clearly selected.
- unchecked: empty square/circle clearly visible and NOT selected.
- not_found: no privacy checkbox near terms text on screen (may be a modal dialog — classify elsewhere).
- uncertain: checkbox area occluded, too small, or ambiguous.
- checkbox_visible=true when you can see the control even if state is uncertain.
- suggested_action=tap_checkbox when unchecked and checkbox is visible; otherwise none.
"""
        prefix = (
            f"[VisionWorker:checkbox] round {round_id}"
            if round_id is not None
            else "[VisionWorker:checkbox]"
        )
        images: list = [prompt]
        if before_screenshot_path is not None:
            images.append(BinaryImage.from_path(before_screenshot_path))
        images.append(BinaryImage.from_path(screenshot_path))

        t0 = time.perf_counter()
        try:
            result = await self._run_agent(images)
            raw = self._require_model_output(result, prefix=prefix)
            judgment = parse_privacy_checkbox_judgment(raw)
            logger.info(
                "%s judgment | %.2fs | state=%s conf=%.2f visible=%s",
                prefix,
                time.perf_counter() - t0,
                judgment.state,
                judgment.confidence,
                judgment.checkbox_visible,
            )
            log_vision_json(logger, prefix, judgment.model_dump(), summary="checkbox judgment full")
            return judgment
        except Exception:
            logger.exception("%s API failed | %.2fs", prefix, time.perf_counter() - t0)
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
        Whether marked tap (red dot/crosshair) hits the checkbox, not agreement text.
        For offline debug images and OCR left-offset alignment checks.
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
            f"[VisionWorker:checkbox_align] round {round_id}"
            if round_id is not None
            else "[VisionWorker:checkbox_align]"
        )
        t0 = time.perf_counter()
        try:
            result = await self._run_agent(
                [prompt, BinaryImage.from_path(screenshot_path)],
            )
            raw = self._require_model_output(result, prefix=prefix)
            judgment = parse_checkbox_tap_alignment(raw)
            logger.info(
                "%s | %.2fs | on_checkbox=%s conf=%.2f dir=%s",
                prefix,
                time.perf_counter() - t0,
                judgment.on_checkbox,
                judgment.confidence,
                judgment.adjust_direction,
            )
            log_vision_json(logger, prefix, judgment.model_dump(), summary="checkbox_align judgment full")
            return judgment
        except Exception:
            logger.exception("%s API failed", prefix)
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


def parse_privacy_gate_judgment(raw: str) -> PrivacyGateJudgment:
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
        gate_kind = str(data.get("gate_kind", "unknown") or "unknown").strip().lower()
        if gate_kind not in ("modal", "checkbox", "none"):
            gate_kind = "unknown"
        try:
            tap_x = int(data.get("tap_x", 0) or 0)
            tap_y = int(data.get("tap_y", 0) or 0)
        except (TypeError, ValueError):
            tap_x, tap_y = 0, 0
        return PrivacyGateJudgment(
            gate_kind=gate_kind,
            confidence=float(data.get("confidence", 0.0) or 0.0),
            tap_x=tap_x,
            tap_y=tap_y,
            tap_label=str(data.get("tap_label", "") or "")[:80],
            reason=str(data.get("reason", "") or "")[:500],
        )
    except Exception:
        return PrivacyGateJudgment(
            gate_kind="unknown",
            confidence=0.0,
            reason=f"Failed to parse model JSON: {text[:300]}",
        )


def parse_download_gate_judgment(raw: str) -> DownloadGateJudgment:
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
        action = str(data.get("action", "wait") or "wait").strip().lower()
        if action not in ("wait", "tap_continue", "done"):
            action = "wait"
        try:
            tap_x = int(data.get("tap_x", 0) or 0)
            tap_y = int(data.get("tap_y", 0) or 0)
        except (TypeError, ValueError):
            tap_x, tap_y = 0, 0
        return DownloadGateJudgment(
            is_download=bool(data.get("is_download", False)),
            in_progress=bool(data.get("in_progress", False)),
            progress_text=str(data.get("progress_text", "") or "")[:40],
            action=action,
            tap_x=tap_x,
            tap_y=tap_y,
            confidence=float(data.get("confidence", 0.0) or 0.0),
            reason=str(data.get("reason", "") or "")[:500],
        )
    except Exception:
        return DownloadGateJudgment(
            is_download=False,
            confidence=0.0,
            reason=f"Failed to parse model JSON: {text[:300]}",
        )


def parse_sub_account_gate_judgment(raw: str) -> SubAccountGateJudgment:
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
        try:
            tap_x = int(data.get("tap_x", 0) or 0)
            tap_y = int(data.get("tap_y", 0) or 0)
        except (TypeError, ValueError):
            tap_x, tap_y = 0, 0
        return SubAccountGateJudgment(
            is_sub_account=bool(data.get("is_sub_account", False)),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            tap_x=tap_x,
            tap_y=tap_y,
            tap_label=str(data.get("tap_label", "") or "")[:80],
            reason=str(data.get("reason", "") or "")[:500],
        )
    except Exception:
        return SubAccountGateJudgment(
            is_sub_account=False,
            confidence=0.0,
            reason=f"Failed to parse model JSON: {text[:300]}",
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
        suggested = str(data.get("suggested_action", "none") or "none").strip().lower()
        if suggested not in ("tap_checkbox", "tap_consent_button", "none"):
            suggested = "none"
        try:
            tap_x = int(data.get("tap_x", 0) or 0)
            tap_y = int(data.get("tap_y", 0) or 0)
        except (TypeError, ValueError):
            tap_x, tap_y = 0, 0
        return PrivacyCheckboxJudgment(
            state=state,
            confidence=float(data.get("confidence", 0.0) or 0.0),
            checkbox_visible=bool(data.get("checkbox_visible", False)),
            reason=str(data.get("reason", "") or "")[:500],
            suggested_action=suggested,
            tap_x=tap_x,
            tap_y=tap_y,
            tap_label=str(data.get("tap_label", "") or "")[:80],
        )
    except Exception:
        return PrivacyCheckboxJudgment(
            state="uncertain",
            confidence=0.0,
            checkbox_visible=False,
            reason=f"Failed to parse model JSON: {text[:300]}",
        )


def parse_scene_gate_judgment(raw: str) -> SceneGateJudgment:
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
        action = str(data.get("action", "none") or "none").strip().lower()
        if action not in ("wait", "tap_dialogue", "tap_skip", "tap_continue", "none"):
            action = "none"
        label_slug = str(data.get("label_slug", "") or "").strip()
        scene_id = str(data.get("scene_id", "unknown") or "unknown")
        if not label_slug and scene_id != "unknown":
            label_slug = scene_id
        coord_strategy = str(data.get("coord_strategy", "") or "").strip().lower()
        if not coord_strategy and action == "wait":
            coord_strategy = "wait"
        if not coord_strategy and action in ("tap_dialogue", "tap_skip", "tap_continue"):
            coord_strategy = "ocr"
        return SceneGateJudgment(
            label_slug=label_slug,
            label_display=str(data.get("label_display", "") or "")[:200],
            coord_strategy=coord_strategy,
            semantic_target=str(data.get("semantic_target", "") or "")[:80],
            match_prior_label_id=str(data.get("match_prior_label_id", "") or "")[:32],
            legacy_scene_hint=str(data.get("legacy_scene_hint", "") or "")[:40],
            scene_id=scene_id,
            confidence=float(data.get("confidence", 0.0) or 0.0),
            description=str(data.get("description", "") or "")[:300],
            action=action,
            reason=str(data.get("reason", "") or "")[:500],
            use_dim_region_tap=bool(data.get("use_dim_region_tap")),
            dim_region_hint=str(data.get("dim_region_hint", "") or "")[:300],
        )
    except Exception:
        return SceneGateJudgment(
            scene_id="unknown",
            confidence=0.0,
            reason=f"Failed to parse model JSON: {text[:300]}",
        )


def _parse_in_game_session_progress(raw: str) -> InGameSessionProgressJudgment:
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
        return InGameSessionProgressJudgment(
            session_progressed=bool(data.get("session_progressed")),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            reason=str(data.get("reason", "") or "")[:500],
        )
    except Exception:
        return InGameSessionProgressJudgment(
            session_progressed=False,
            confidence=0.0,
            reason=f"Failed to parse model JSON: {text[:300]}",
        )


def _parse_tutorial_pulse_pick(raw: str) -> TutorialPulsePick:
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
        band = str(data.get("preferred_band", "") or "").strip().lower()
        if band not in ("top", "middle", "lower", ""):
            band = ""
        reject = data.get("reject_ranks") or []
        reject_ranks = [int(x) for x in reject if isinstance(x, (int, float, str)) and str(x).isdigit()]
        return TutorialPulsePick(
            forced_guidance_present=bool(data.get("forced_guidance_present")),
            chosen_pulse_rank=int(data.get("chosen_pulse_rank", 0) or 0),
            reject_ranks=reject_ranks,
            preferred_band=band,  # type: ignore[arg-type]
            target_description=str(data.get("target_description", "") or "")[:200],
            confidence=float(data.get("confidence", 0.0) or 0.0),
            reason=str(data.get("reason", "") or "")[:500],
        )
    except Exception:
        return TutorialPulsePick(
            confidence=0.0,
            reason=f"Failed to parse model JSON: {text[:300]}",
        )


def _parse_in_game_screen_analysis(raw: str) -> InGameScreenAnalysis:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return InGameScreenAnalysis.model_validate(json.loads(text))
    except Exception:
        return InGameScreenAnalysis(
            confidence=0.0,
            observations=f"Failed to parse model JSON: {text[:300]}",
            analysis="parse error",
        )


def _parse_game_entry_judgment(raw: str) -> GameEntryJudgment:
    text = (raw or "").strip()
    if not text:
        return GameEntryJudgment(
            in_game_main=False,
            confidence=0.0,
            stage="unknown",
            reason="Multimodal API returned empty output",
        )
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
