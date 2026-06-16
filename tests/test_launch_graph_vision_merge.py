from __future__ import annotations

from game_agent.graphs.launch_facts import merge_vision_into_facts
from game_agent.models.launch_graph_state import LaunchFacts


def test_merge_vision_download_stage() -> None:
    facts = LaunchFacts()
    raw = '{"stage": "resource_download", "has_anomaly": false, "progress": "45%"}'
    merged = merge_vision_into_facts(facts, raw)
    assert merged.download_visible is True
    assert merged.vision_stage == "resource_download"


def test_merge_vision_login_does_not_override_sub_account() -> None:
    facts = LaunchFacts(sub_account_blocking=True, login_stage="sub_account_select")
    raw = '{"stage": "login", "has_anomaly": false}'
    merged = merge_vision_into_facts(facts, raw)
    assert merged.sub_account_blocking is True
    assert merged.login_blocking is False
