from __future__ import annotations

import json

from game_agent.graphs.launch_facts import merge_analyze_screen_response
from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.models.vision_tool_result import VisionToolErrorCode, format_vision_tool_response


def test_merge_analyze_screen_response_updates_facts() -> None:
    facts = LaunchFacts()
    payload = format_vision_tool_response(
        error_code=VisionToolErrorCode.OK,
        data={
            "stage": "login",
            "has_anomaly": False,
            "anomaly_reason": "",
            "progress": "",
        },
    )
    merged, hint = merge_analyze_screen_response(facts, payload)
    assert merged.login_blocking is True
    assert merged.vision_stage == "login"
    assert "vision_stage=login" in hint


def test_merge_analyze_screen_response_keeps_facts_on_error() -> None:
    facts = LaunchFacts(enter_cta_visible=True)
    payload = format_vision_tool_response(
        error_code=VisionToolErrorCode.NO_MULTIMODAL,
        error_message="no multimodal",
    )
    merged, hint = merge_analyze_screen_response(facts, payload)
    assert merged.enter_cta_visible is True
    assert "no multimodal" in hint


def test_merge_analyze_screen_anomaly_in_hint() -> None:
    facts = LaunchFacts()
    body = {
        "errorCode": 0,
        "errorMessage": "",
        "completed": True,
        "data": {
            "stage": "unknown",
            "has_anomaly": True,
            "anomaly_reason": "网络连接失败",
            "progress": "",
        },
    }
    merged, hint = merge_analyze_screen_response(facts, json.dumps(body))
    assert merged.vision_has_anomaly is True
    assert "网络连接失败" in hint
