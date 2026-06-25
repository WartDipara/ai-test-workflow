"""节点内 ActionFrame：Act → Observe → Verify → Reflect → Correct。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from game_agent.models.launch_graph_state import LaunchGraphState
from game_agent.services.action_correction import apply_correction
from game_agent.services.action_reflection import ActionReflection, reflect_on_failure
from game_agent.services.node_verifier import NodeVerifyResult
from game_agent.utils.ocr_util import OcrBbox, run_ocr_frame, serialize_bboxes

logger = logging.getLogger(__name__)


class ActionFailureTrace(BaseModel):
    node: str = ""
    attempt: int = 0
    reason: str = ""
    root_cause: str = ""
    ocr_excerpt: str = ""
    artifact: str = ""
    evidence: str = ""


class ActionFrameResult(BaseModel):
    passed: bool = False
    attempts: int = 0
    artifact: str = ""
    evidence: str = ""
    failure_traces: list[ActionFailureTrace] = Field(default_factory=list)
    last_reflection: ActionReflection | None = None


class AdbLike(Protocol):
    def tap(self, x: int, y: int, *, width: int, height: int) -> str: ...
    def wait_seconds(self, seconds: float) -> str: ...
    def screencap_png(self, path: Path) -> None: ...

    @property
    def device_serial(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ObserveCapture:
    screenshot: str
    ocr_summary: str
    bboxes: list[OcrBbox]


ActFn = Callable[[LaunchGraphState, int], Awaitable[str]]
VerifyFn = Callable[[LaunchGraphState, str, str], NodeVerifyResult]


async def capture_observe(
    adb: AdbLike,
    artifact_root: Path,
    *,
    prefix: str,
    screen_w: int,
    screen_h: int,
    wait_s: float = 0.8,
) -> ObserveCapture:
    if wait_s > 0:
        adb.wait_seconds(wait_s)
    ts = datetime.now().strftime("%H%M%S_%f")
    shot = artifact_root / f"{prefix}_{ts}.png"
    adb.screencap_png(shot)
    ocr_summary, bboxes = await asyncio.to_thread(
        run_ocr_frame,
        shot,
        device_w=screen_w,
        device_h=screen_h,
        worker_key=adb.device_serial,
    )
    return ObserveCapture(
        screenshot=str(shot.resolve()),
        ocr_summary=ocr_summary,
        bboxes=bboxes,
    )


def record_action_failure(
    state: LaunchGraphState,
    trace: ActionFailureTrace,
) -> None:
    raw = state.get("action_failure_trace") or []
    traces = list(raw) if isinstance(raw, list) else []
    traces.append(trace.model_dump())
    state["action_failure_trace"] = traces[-20:]


def note_action_failure(
    state: LaunchGraphState,
    *,
    node: str,
    verify: NodeVerifyResult,
    ocr_before: str,
    ocr_after: str,
    attempt: int = 1,
    artifact: str = "",
    expected_stage: str = "",
) -> ActionReflection:
    """记录旁路/复杂节点失败并返回检讨结果。"""
    from game_agent.models.launch_graph_state import facts_from_state

    facts = facts_from_state(state)
    reflection = reflect_on_failure(
        node=node,
        verify=verify,
        ocr_before=ocr_before,
        ocr_after=ocr_after,
        facts=facts,
        expected_stage=expected_stage,
    )
    state["last_reflection"] = reflection.model_dump()
    record_action_failure(
        state,
        ActionFailureTrace(
            node=node,
            attempt=attempt,
            reason=verify.reason,
            root_cause=reflection.root_cause,
            ocr_excerpt=(ocr_after or "")[:200],
            artifact=artifact,
            evidence=verify.evidence,
        ),
    )
    return reflection


async def run_action_frame(
    state: LaunchGraphState,
    *,
    node: str,
    adb: AdbLike,
    artifact_root: Path,
    screen_w: int,
    screen_h: int,
    act_fn: ActFn,
    verify_fn: VerifyFn,
    max_attempts: int = 3,
    ocr_before: str = "",
    expected_stage: str = "",
    attempt_context: Any | None = None,
) -> ActionFrameResult:
    """
    节点内微循环：执行 act_fn → 截图 OCR → verify_fn。
    失败时 reflect + correct，在本节点内重试；不 mark 里程碑完成。
    """
    from game_agent.models.launch_graph_state import facts_from_state

    result = ActionFrameResult()
    before_ocr = ocr_before or str(state.get("last_ocr_summary") or "")
    traces: list[ActionFailureTrace] = []

    for attempt in range(1, max(1, max_attempts) + 1):
        result.attempts = attempt
        act_msg = await act_fn(state, attempt)
        logger.info("[ActionFrame:%s] act attempt=%d %s", node, attempt, (act_msg or "")[:160])
        if attempt_context is not None:
            attempt_context.set_ocr_busy(True)
        try:
            observed = await capture_observe(
                adb,
                artifact_root,
                prefix=f"frame_{node}",
                screen_w=screen_w,
                screen_h=screen_h,
            )
        finally:
            if attempt_context is not None:
                attempt_context.set_ocr_busy(False)

        state["last_screenshot"] = observed.screenshot
        state["last_ocr_summary"] = observed.ocr_summary
        state["last_bboxes"] = serialize_bboxes(observed.bboxes)

        verify = verify_fn(state, before_ocr, observed.ocr_summary)
        if verify.passed:
            result.passed = True
            result.artifact = act_msg
            result.evidence = verify.evidence or verify.reason
            result.failure_traces = traces
            logger.info(
                "[ActionFrame:%s] verify_pass attempt=%d %s",
                node,
                attempt,
                verify.reason[:120],
            )
            return result

        facts = facts_from_state(state)
        reflection = reflect_on_failure(
            node=node,
            verify=verify,
            ocr_before=before_ocr,
            ocr_after=observed.ocr_summary,
            facts=facts,
            expected_stage=expected_stage,
        )
        result.last_reflection = reflection
        state["last_reflection"] = reflection.model_dump()

        trace = ActionFailureTrace(
            node=node,
            attempt=attempt,
            reason=verify.reason,
            root_cause=reflection.root_cause,
            ocr_excerpt=(observed.ocr_summary or "")[:200],
            artifact=observed.screenshot,
            evidence=verify.evidence,
        )
        traces.append(trace)
        record_action_failure(state, trace)

        logger.info(
            "[ActionFrame:%s] verify_fail attempt=%d cause=%s %s",
            node,
            attempt,
            reflection.root_cause,
            verify.reason[:120],
        )

        if attempt >= max_attempts:
            break

        apply_correction(state, reflection)
        if reflection.root_cause == "wrong_route":
            # 误路由：本节点内不再重试，交给下轮 plan_route + DFS
            break
        if reflection.wait_s > 0:
            adb.wait_seconds(reflection.wait_s)
        before_ocr = observed.ocr_summary

    result.failure_traces = traces
    return result
