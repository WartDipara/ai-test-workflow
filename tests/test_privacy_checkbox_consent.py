from __future__ import annotations

from game_agent.models.privacy_checkbox_judgment import PrivacyCheckboxJudgment


def test_suggests_consent_button_requires_high_confidence() -> None:
    j = PrivacyCheckboxJudgment(
        state="not_found",
        confidence=0.9,
        suggested_action="tap_consent_button",
        tap_x=100,
        tap_y=200,
    )
    assert j.suggests_consent_button(min_confidence=0.55) is True
    low = j.model_copy(update={"confidence": 0.4})
    assert low.suggests_consent_button(min_confidence=0.55) is False
