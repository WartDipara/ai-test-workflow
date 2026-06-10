from __future__ import annotations

from enum import Enum


class PipelinePhase(str, Enum):
    ORCHESTRATOR = "orchestrator"
    PREPROCESS = "preprocess"
    INIT = "init"
    MODIFY = "modify"
    EXECUTOR = "executor"
    OBSERVER = "observer"
    CLEANUP = "cleanup"

