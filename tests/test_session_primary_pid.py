from __future__ import annotations

from game_agent.services.game_launch import (
    package_primary_pid_changed,
    primary_package_pid,
)


def test_primary_package_pid_ignores_child_spawn() -> None:
    assert primary_package_pid(frozenset({"1202"})) == "1202"
    assert primary_package_pid(frozenset({"1202", "27113"})) == "1202"
    assert not package_primary_pid_changed(
        frozenset({"1202"}),
        frozenset({"1202", "27113"}),
    )


def test_primary_package_pid_ignores_child_exit() -> None:
    assert not package_primary_pid_changed(
        frozenset({"1202", "27113"}),
        frozenset({"1202"}),
    )


def test_primary_package_pid_detects_main_process_change() -> None:
    assert package_primary_pid_changed(
        frozenset({"1202"}),
        frozenset({"54321"}),
    )
    assert package_primary_pid_changed(
        frozenset({"1202", "27113"}),
        frozenset({"54321", "27113"}),
    )


def test_primary_package_pid_empty_sets() -> None:
    assert primary_package_pid(frozenset()) is None
    assert not package_primary_pid_changed(frozenset({"1202"}), frozenset())
