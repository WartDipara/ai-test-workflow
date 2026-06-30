from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from game_agent.services.behavior_chain import BehaviorChain, BehaviorStep

VerdictType = Literal["continue", "success", "fail", "wait"]
RetryStrategy = Literal["none", "replan", "retry_step", "wait_loading"]
StepCoordSource = Literal["ocr", "pulse", "vlm_xy", "dialogue_blank", ""]


class InGameSessionStepPlan(BaseModel):
    id: str = "step_1"
    action: Literal["tap_xy", "tap_text", "swipe", "press_back", "wait", "none"] = "none"
    x: int = 0
    y: int = 0
    x2: int = 0
    y2: int = 0
    target_text: str = ""
    coord_source: StepCoordSource = ""
    wait_s: float = 1.5
    intent: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    reason: str = ""


class InGameSessionPlan(BaseModel):
    """主脑单轮结构化输出（pydantic_ai output_type）。"""

    verdict: VerdictType = "continue"
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    reason: str = ""
    analysis: str = ""
    retry_strategy: RetryStrategy = "none"
    wait_s: float = Field(default=2.0, ge=0.5, le=30.0)
    goal: str = ""
    steps: list[InGameSessionStepPlan] = Field(default_factory=list)


class InGameSessionDecision(BaseModel):
    """主脑决策结果（含可选行为链）。"""

    verdict: VerdictType
    confidence: float = 0.0
    reason: str = ""
    analysis: str = ""
    retry_strategy: RetryStrategy = "none"
    wait_s: float = 2.0
    behavior_chain: BehaviorChain | None = None
    source: str = "brain"


def plan_to_decision(
    plan: InGameSessionPlan,
    *,
    chain: BehaviorChain | None = None,
    source: str = "brain",
) -> InGameSessionDecision:
    return InGameSessionDecision(
        verdict=plan.verdict,
        confidence=plan.confidence,
        reason=plan.reason,
        analysis=plan.analysis,
        retry_strategy=plan.retry_strategy,
        wait_s=plan.wait_s,
        behavior_chain=chain,
        source=source,
    )


def steps_plan_to_chain(
    plan: InGameSessionPlan,
    *,
    bboxes: list | None = None,
    screen_w: int,
    screen_h: int,
    max_wait_s: float,
) -> BehaviorChain | None:
    if not plan.steps:
        return None
    from game_agent.services.behavior_chain import clamp_coord, validate_behavior_chain

    steps: list[BehaviorStep] = []
    for i, sp in enumerate(plan.steps[:7]):
        step_id = (sp.id or f"step_{i + 1}").strip()[:40]
        wait_s = max(0.5, min(float(sp.wait_s or 1.5), max_wait_s))
        steps.append(
            BehaviorStep(
                id=step_id,
                action=sp.action,
                x=clamp_coord(int(sp.x), screen_w),
                y=clamp_coord(int(sp.y), screen_h),
                x2=clamp_coord(int(sp.x2), screen_w),
                y2=clamp_coord(int(sp.y2), screen_h),
                target_text=str(sp.target_text or "")[:80],
                wait_s=wait_s,
                intent=str(sp.intent or "")[:200],
                coord_source=str(sp.coord_source or "")[:20],  # type: ignore[arg-type]
                success_criteria=list(sp.success_criteria or [])[:5],
                reason=str(sp.reason or "")[:300],
            ),
        )
    chain = BehaviorChain(
        steps=steps,
        source="brain",
        stage="in_game",
        goal=str(plan.goal or "")[:200],
    )
    return validate_behavior_chain(
        chain,
        bboxes=bboxes or [],
        screen_w=screen_w,
        screen_h=screen_h,
    )
