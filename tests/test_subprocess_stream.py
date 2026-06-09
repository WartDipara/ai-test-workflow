from __future__ import annotations

import sys
from unittest.mock import patch

from game_agent.services.subprocess_tree import popen_communicate_poll


def test_popen_stream_output_logs_lines() -> None:
    cmd = [
        sys.executable,
        "-c",
        "import sys; print('line-one'); print('line-two', file=sys.stderr)",
    ]
    with patch("game_agent.services.subprocess_tree.logger") as log_mock:
        result = popen_communicate_poll(cmd, stream_output=True, stream_prefix="[test]")
    assert result.returncode == 0
    assert b"line-one" in result.stdout
    assert b"line-two" in result.stderr
    messages = [str(c) for c in log_mock.info.call_args_list]
    assert any("line-one" in m for m in messages)
    assert any("line-two" in m for m in messages)
