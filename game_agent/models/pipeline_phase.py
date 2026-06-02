from __future__ import annotations

from enum import Enum


class PipelinePhase(str, Enum):
    INIT = "init"
    MODIFY = "modify"
    KEYWIZARD = "keywizard"
    GAME_ENTRY = "game_entry"
    OBSERVER = "observer"
    CLEANUP = "cleanup"

