from game_agent.models.run_state import RunState


def test_launch_stage_defaults() -> None:
    run = RunState()
    assert run.launch_stage == "launch"
    assert run.server_checked is False
    assert run.server_check_attempts == 0
    assert run.last_stage_error == ""


def test_format_launch_stage_with_error() -> None:
    run = RunState(
        launch_stage="server_check",
        server_checked=False,
        server_check_attempts=3,
        last_stage_error="selector did not open",
    )
    text = run.format_launch_stage_status()
    assert "last_error='selector did not open'" in text
