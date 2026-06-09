from pathlib import Path

import pytest

from game_agent.utils.gameturbo_bootstrap import parse_gid_from_apk_name


def test_parse_gid_from_apk_name_ok() -> None:
    assert parse_gid_from_apk_name(Path("12345_foo.apk")) == "12345"


def test_parse_gid_from_apk_name_error() -> None:
    with pytest.raises(RuntimeError, match="gid"):
        parse_gid_from_apk_name(Path("no_gid.apk"))
