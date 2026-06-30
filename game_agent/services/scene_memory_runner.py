"""局内场景记忆 FastPath 执行（委托 SceneLabelRegistry）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from game_agent.models.launch_graph_state import LaunchGraphState
from game_agent.utils.ocr_util import OcrBbox

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SceneMemoryFastPathResult:
    handled: bool
    success: bool = False
    message: str = ""


async def try_scene_memory_fast_path(
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
) -> SceneMemoryFastPathResult:
    """兼容封装：委托 SceneLabel fast path。"""
    from game_agent.services.scene_label_runner import try_scene_label_fast_path

    result = await try_scene_label_fast_path(
        state,
        shot=shot,
        ocr_summary=ocr_summary,
        bboxes=bboxes,
        round_id=agent_rounds,
        sw=sw,
        sh=sh,
        artifact_root=artifact_root,
        adb=adb,
        actx=actx,
        audit=audit,
        scope="in_game",
        node=node,
    )
    return SceneMemoryFastPathResult(
        handled=result.handled,
        success=result.success,
        message=result.message,
    )


def learn_scene_memory_after_step(
    state: LaunchGraphState,
    *,
    artifact_root: Path,
    before_ocr: str,
    after_ocr: str,
    bboxes: list[OcrBbox],
    step,
    round_id: int,
    screenshot_ref: str,
    screen_w: int,
    screen_h: int,
    screen_analysis=None,
    step_passed: bool,
) -> None:
    """慢路径：验证进展后写入 scene label 注册表。"""
    from game_agent.services.scene_label_runner import learn_scene_label_after_step

    learn_scene_label_after_step(
        state,
        artifact_root=artifact_root,
        before_ocr=before_ocr,
        after_ocr=after_ocr,
        bboxes=bboxes,
        step=step,
        round_id=round_id,
        screenshot_ref=screenshot_ref,
        screen_w=screen_w,
        screen_h=screen_h,
        screen_analysis=screen_analysis,
        step_passed=step_passed,
        scope="in_game",
    )
