"""局内 OCR 启发式快路径：纯对话/空白继续，跳过 VLM+主脑。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from game_agent.graphs.launch_state_store import mark_tree_node_done
from game_agent.models.launch_graph_state import LaunchGraphState
from game_agent.models.scene import SceneTransition
from game_agent.services.dialogue_heuristics import (
    ocr_has_blank_continue_cta,
    score_dialogue_from_bboxes,
)
from game_agent.services.scene_strategies import plan_dialogue_action
from game_agent.services.tutorial_intent import (
    has_pulse_guidance_phrase,
    needs_visual_tap_locator,
)
from game_agent.utils.ocr_util import OcrBbox, run_ocr_frame

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InGameHeuristicFastPathResult:
    handled: bool
    success: bool = False
    message: str = ""


def should_use_in_game_heuristic_fast_path(
    ocr_summary: str,
    bboxes: list[OcrBbox],
    *,
    screen_h: int,
) -> bool:
    """简单对话推进：无战斗脉冲/卡牌类强制引导。"""
    if has_pulse_guidance_phrase(ocr_summary):
        return False
    if needs_visual_tap_locator(ocr_summary, bboxes):
        return False
    if bboxes and ocr_has_blank_continue_cta(bboxes):
        return True
    if screen_h > 0:
        score, _ = score_dialogue_from_bboxes(bboxes, screen_h=screen_h)
        if score >= 0.55:
            return True
    return False


async def try_in_game_heuristic_fast_path(
    state: LaunchGraphState,
    *,
    shot: Path,
    ocr_summary: str,
    bboxes: list[OcrBbox],
    agent_rounds: int,
    sw: int,
    sh: int,
    artifact_root: Path,
    adb,
    actx,
    audit,
    node: str = "in_game_agent",
    fast_path_enabled: bool = True,
) -> InGameHeuristicFastPathResult:
    if not fast_path_enabled:
        return InGameHeuristicFastPathResult(handled=False)
    if not should_use_in_game_heuristic_fast_path(ocr_summary, bboxes, screen_h=sh):
        return InGameHeuristicFastPathResult(handled=False)

    transition = SceneTransition(kind="none", reason="", from_scene="", to_scene="")
    plan = plan_dialogue_action(
        bboxes,
        ocr_summary=ocr_summary,
        screen_w=sw,
        screen_h=sh,
        transition=transition,
    )
    if plan.action != "tap_xy" or plan.x <= 0 or plan.y <= 0:
        return InGameHeuristicFastPathResult(handled=False, message="no_tap_plan")

    exec_msg = adb.tap(plan.x, plan.y, width=sw, height=sh)
    adb.wait_seconds(0.6)
    state["in_game_agent_last_action_at"] = time.monotonic()

    ts = datetime.now().strftime("%H%M%S_%f")
    after_shot = artifact_root / f"graph_heuristic_fast_{agent_rounds:03d}_{ts}.png"
    adb.screencap_png(after_shot)
    if actx is not None:
        actx.set_ocr_busy(True)
    try:
        after_ocr, _ = await asyncio.to_thread(
            run_ocr_frame,
            after_shot,
            device_w=sw,
            device_h=sh,
            worker_key=adb.device_serial,
        )
    finally:
        if actx is not None:
            actx.set_ocr_busy(False)

    progressed = (ocr_summary or "").strip() != (after_ocr or "").strip()
    if progressed:
        state["last_screenshot"] = str(after_shot.resolve())
        state["last_ocr_summary"] = after_ocr
        state["in_game_play_steps_executed"] = int(state.get("in_game_play_steps_executed") or 0) + 1
        logger.info(
            "[InGameHeuristic] fast_path ok (%d,%d) reason=%s | %s",
            plan.x,
            plan.y,
            plan.reason[:60],
            str(exec_msg)[:80],
        )
        if audit is not None:
            audit.log_phase(
                "in_game_play",
                "heuristic_fast_path",
                tap_x=plan.x,
                tap_y=plan.y,
                reason=plan.reason[:120],
            )
        mark_tree_node_done(
            state,
            node,
            artifact=str(after_shot.resolve()),
            evidence=f"heuristic:{plan.reason[:80]}",
        )
        return InGameHeuristicFastPathResult(handled=True, success=True, message=str(exec_msg)[:120])

    logger.info(
        "[InGameHeuristic] fast_path miss (%d,%d) no OCR change | %s",
        plan.x,
        plan.y,
        plan.reason[:60],
    )
    return InGameHeuristicFastPathResult(handled=False, message="heuristic_no_progress")
