from __future__ import annotations

from game_agent.workers.vision_worker import (
    parse_checkbox_tap_alignment,
    parse_privacy_checkbox_judgment,
)


def test_parse_privacy_checkbox_judgment_checked() -> None:
    raw = '{"state": "checked", "confidence": 0.91, "checkbox_visible": true, "reason": "tick visible"}'
    j = parse_privacy_checkbox_judgment(raw)
    assert j.is_checked
    assert j.confidence == 0.91
    assert j.checkbox_visible is True


def test_parse_privacy_checkbox_judgment_fence() -> None:
    raw = """```json
{"state": "unchecked", "confidence": 0.8, "checkbox_visible": true, "reason": "empty box"}
```"""
    j = parse_privacy_checkbox_judgment(raw)
    assert j.is_unchecked
    assert j.state == "unchecked"


def test_parse_privacy_checkbox_judgment_invalid_json() -> None:
    j = parse_privacy_checkbox_judgment("not json")
    assert j.state == "uncertain"
    assert j.confidence == 0.0


def test_parse_privacy_checkbox_judgment_unknown_state() -> None:
    j = parse_privacy_checkbox_judgment(
        '{"state": "maybe", "confidence": 0.5, "checkbox_visible": false, "reason": "x"}'
    )
    assert j.state == "uncertain"


def test_parse_checkbox_tap_alignment_on_checkbox() -> None:
    raw = (
        '{"on_checkbox": true, "confidence": 0.92, '
        '"reason": "red dot on square", "adjust_direction": "ok"}'
    )
    j = parse_checkbox_tap_alignment(raw)
    assert j.on_checkbox is True
    assert j.confidence == 0.92
    assert j.adjust_direction == "ok"


def test_parse_checkbox_tap_alignment_needs_left() -> None:
    raw = (
        '{"on_checkbox": false, "confidence": 0.88, '
        '"reason": "marker on 我", "adjust_direction": "left"}'
    )
    j = parse_checkbox_tap_alignment(raw)
    assert j.on_checkbox is False
    assert j.adjust_direction == "left"


def test_parse_checkbox_tap_alignment_invalid_json() -> None:
    j = parse_checkbox_tap_alignment("not json")
    assert j.on_checkbox is False
    assert j.confidence == 0.0
