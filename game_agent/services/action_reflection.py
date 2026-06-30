"""ActionFrame 失败检讨：规则优先，可选 VLM。"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.services.node_verifier import NodeVerifyResult
from game_agent.services.privacy_gate import ocr_has_privacy_context, privacy_modal_still_open
from game_agent.i18n import Concept, compile_lexicon_pattern

ActionRootCause = Literal[
    "wrong_coords",
    "wrong_route",
    "ocr_misread",
    "timing",
    "unknown",
]

_PRIVACY_TERMS_RE = compile_lexicon_pattern(Concept.PRIVACY, Concept.PRIVACY_TERMS)
_DOWNLOAD_PROGRESS_RE = re.compile(
    compile_lexicon_pattern(Concept.DOWNLOAD_UPDATING, Concept.DOWNLOAD_STRONG).pattern
    + r"|\d+\s*%",
    re.IGNORECASE,
)


class ActionReflection(BaseModel):
    root_cause: ActionRootCause = "unknown"
    reason: str = ""
    recover_hint: str = ""
    fact_patches: dict[str, Any] = Field(default_factory=dict)
    retry_coords: tuple[int, int] | None = None
    wait_s: float = 0.0


def reflect_on_failure(
    *,
    node: str,
    verify: NodeVerifyResult,
    ocr_before: str,
    ocr_after: str,
    facts: LaunchFacts,
    expected_stage: str = "",
) -> ActionReflection:
    """基于规则检讨节点内失败原因（不重新规划整张图）。"""
    stage = (expected_stage or "").strip().lower()
    merged_after = ocr_after or ""
    merged_before = ocr_before or ""

    if node == "handle_download":
        privacy_on_screen = ocr_has_privacy_context(merged_after) or bool(
            _PRIVACY_TERMS_RE.search(merged_after)
        )
        if privacy_on_screen and not _DOWNLOAD_PROGRESS_RE.search(merged_after):
            return ActionReflection(
                root_cause="wrong_route",
                reason="download node but privacy modal still visible",
                recover_hint="restore privacy milestone routing",
                fact_patches={
                    "download_visible": False,
                    "download_in_progress": False,
                    "download_gate_kind": "",
                    "download_action": "",
                    "initial_privacy_dialog": True,
                    "privacy_gate_kind": "modal",
                },
            )

    if stage == "privacy_modal":
        if privacy_modal_still_open(merged_after):
            if facts.agree_button_xy is None:
                return ActionReflection(
                    root_cause="wrong_coords",
                    reason="privacy modal still visible, missing agree coords",
                    recover_hint="re-resolve privacy gate coords",
                )
            return ActionReflection(
                root_cause="wrong_coords",
                reason=verify.reason or "privacy modal consent row still visible",
                recover_hint="retry agree tap or OCR pick",
                retry_coords=facts.agree_button_xy,
                wait_s=1.2,
            )
        if facts.download_visible and ocr_has_privacy_context(merged_before):
            return ActionReflection(
                root_cause="wrong_route",
                reason="privacy cleared but download_visible still set",
                recover_hint="clear download_visible",
                fact_patches={
                    "download_visible": False,
                    "download_in_progress": False,
                },
            )

    if stage in ("sub_account_select", "sub_account"):
        return ActionReflection(
            root_cause="wrong_coords",
            reason=verify.reason or "still on sub-account screen",
            recover_hint=f"re-match sub_account target: {verify.evidence}"[:200],
            wait_s=1.2,
        )

    if merged_before.strip() == merged_after.strip():
        return ActionReflection(
            root_cause="timing",
            reason=verify.reason or "no OCR change after action",
            recover_hint="wait and retry",
            wait_s=1.5,
        )

    if stage == "login" and "login form still visible" in (verify.reason or ""):
        return ActionReflection(
            root_cause="timing",
            reason=verify.reason,
            recover_hint="login form slow to dismiss",
            wait_s=2.0,
        )

    return ActionReflection(
        root_cause="unknown",
        reason=verify.reason or "verify failed",
        recover_hint=f"{node}: {verify.evidence}"[:200],
    )
