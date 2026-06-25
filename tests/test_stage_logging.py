from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path

from game_agent.models.pipeline_phase import PipelinePhase
from game_agent.external_services.gameturbo.log import (
    append_gameturbo_stage_marker,
    gameturbo_log_dedup_key,
    gameturbo_log_path,
)
from game_agent.utils.stage_logging import (
    PipelineStageFilter,
    bind_pipeline_stage,
    get_pipeline_stage,
    pipeline_stage,
    reset_pipeline_stage,
)


def test_pipeline_stage_filter_injects_stage() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    token = bind_pipeline_stage("modify")
    try:
        assert PipelineStageFilter().filter(record) is True
        assert record.pipeline_stage == "modify"
    finally:
        reset_pipeline_stage(token)


def test_pipeline_stage_context_manager_nested() -> None:
    assert get_pipeline_stage() == "-"
    with pipeline_stage("orchestrator"):
        assert get_pipeline_stage() == "orchestrator"
        with pipeline_stage("init"):
            assert get_pipeline_stage() == "init"
        assert get_pipeline_stage() == "orchestrator"
    assert get_pipeline_stage() == "-"


def test_stage_log_format_in_handler_output() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(pipeline_stage)s] %(levelname)s %(name)s: %(message)s",
        ),
    )
    handler.addFilter(PipelineStageFilter())
    logger = logging.getLogger("test.stage_logging")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with pipeline_stage(PipelinePhase.MODIFY.value):
        logger.info("patch proposed")

    out = stream.getvalue()
    assert "[modify]" in out
    assert "patch proposed" in out


def test_append_gameturbo_stage_marker(tmp_path: Path) -> None:
    append_gameturbo_stage_marker(
        tmp_path,
        PipelinePhase.EXECUTOR.value,
        "executor thread start",
    )
    path = gameturbo_log_path(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "# [STAGE:executor] executor thread start" in text

    append_gameturbo_stage_marker(
        tmp_path,
        PipelinePhase.EXECUTOR.value,
        "executor thread start",
    )
    assert text.count("# [STAGE:executor]") == 1


def test_gameturbo_stage_marker_dedup_key() -> None:
    line = "# [STAGE:modify] modify retry"
    assert gameturbo_log_dedup_key(line).startswith("__no_ts__:")
