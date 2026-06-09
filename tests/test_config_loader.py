import os

from game_agent.config.loader import expand_env_strings


def test_expand_env_strings() -> None:
    os.environ["TEST_GAME_AGENT_KEY"] = "secret"
    try:
        assert expand_env_strings("${TEST_GAME_AGENT_KEY}") == "secret"
        assert expand_env_strings({"k": "${TEST_GAME_AGENT_KEY}"}) == {"k": "secret"}
    finally:
        os.environ.pop("TEST_GAME_AGENT_KEY", None)
