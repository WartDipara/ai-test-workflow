"""阶段完成规则与动作执行（模板级，无业务分支）。"""

from __future__ import annotations

from game_agent.models.phase_template import ActionKind, CompletionRule, PhaseSpec


def execute_phase_action(
    spec: PhaseSpec,
    *,
    adb,
    sw: int,
    sh: int,
) -> tuple[str, bool]:
    """执行白名单动作，返回 (消息, 是否已执行)。"""
    action: ActionKind = spec.action
    if action == "tap_xy":
        if spec.x <= 0 or spec.y <= 0:
            return f"refused tap invalid ({spec.x},{spec.y})", False
        return adb.tap(spec.x, spec.y, width=sw, height=sh), True
    if action == "press_back":
        return adb.press_back(), True
    if action == "wait":
        return adb.wait_seconds(spec.wait_s), True
    return "no-op", action == "none"


def evaluate_phase_complete(
    spec: PhaseSpec,
    *,
    entry_fingerprint: str,
    after_fingerprint: str,
    ocr_summary: str,
    action_executed: bool,
) -> bool:
    return spec.complete.evaluate(
        entry_fingerprint=entry_fingerprint,
        after_fingerprint=after_fingerprint,
        ocr_summary=ocr_summary,
        action=spec.action,
        action_executed=action_executed,
    )
