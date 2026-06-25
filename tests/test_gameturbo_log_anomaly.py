from game_agent.external_services.gameturbo.log_anomaly import is_fatal_gameturbo_log_line


def test_bhook_ok_not_fatal() -> None:
    assert is_fatal_gameturbo_log_line("[BHOOK] OK: shutdown in libnetdutils.so") is False


def test_tunnel_closed_fatal() -> None:
    assert is_fatal_gameturbo_log_line("GameTurbo: tunnel closed by peer") is True


def test_empty_line_not_fatal() -> None:
    assert is_fatal_gameturbo_log_line("") is False
