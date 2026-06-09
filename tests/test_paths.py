from pathlib import Path

from game_agent.config.paths import resolve_repo_path
from game_agent.paths import REPO_ROOT


def test_resolve_repo_path_relative() -> None:
    resolved = resolve_repo_path(Path("artifacts"))
    assert resolved == (REPO_ROOT / "artifacts").resolve()


def test_resolve_repo_path_absolute(tmp_path: Path) -> None:
    resolved = resolve_repo_path(tmp_path)
    assert resolved == tmp_path.resolve()
