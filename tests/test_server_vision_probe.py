from game_agent.services.server_vision_probe import parse_server_connectivity_probe


def test_parse_fail_fast_on_network_error() -> None:
    probe = parse_server_connectivity_probe(
        '{"on_enter_game_screen":true,"enter_button_visible":true,'
        '"server_slot_status":"error","has_network_error_ui":true,'
        '"confidence":0.9,"reason":"server fetch failed","recommendation":"fail_fast"}'
    )
    assert probe.recommendation == "fail_fast"
    assert probe.server_slot_status == "error"


def test_parse_wrong_stage_derived() -> None:
    probe = parse_server_connectivity_probe(
        '{"on_enter_game_screen":false,"enter_button_visible":false,'
        '"server_slot_status":"not_visible","confidence":0.8,"reason":"login screen"}'
    )
    assert probe.recommendation == "wrong_stage"


def test_parse_empty_slot_tap_verify() -> None:
    probe = parse_server_connectivity_probe(
        '{"on_enter_game_screen":true,"enter_button_visible":true,'
        '"server_slot_status":"empty","has_network_error_ui":false,'
        '"recommendation":"tap_verify"}'
    )
    assert probe.recommendation == "tap_verify"
    assert probe.server_slot_status == "empty"
