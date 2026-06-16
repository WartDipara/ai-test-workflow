from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from game_agent.graphs.launch_deps import LaunchGraphDeps
from game_agent.graphs.launch_flow import build_launch_graph
from game_agent.models.run_state import RunState
def test_build_launch_graph_compiles() -> None:
    deps = LaunchGraphDeps(
        app_config=MagicMock(),
        adb=MagicMock(),
        run_state=RunState(),
        artifact_root=Path("/tmp/graph_test"),
        settings_path=Path("/tmp/settings.yaml"),
    )
    graph = build_launch_graph(deps)
    assert graph is not None
    assert hasattr(graph, "ainvoke")
