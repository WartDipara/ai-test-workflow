"""Map OCR / failure blobs to canonical pipeline stage ids."""

from __future__ import annotations

from game_agent.i18n.lexicon import Concept
from game_agent.i18n.match import normalize_match_blob, text_contains

_BLOB_STAGE_PRIORITY: tuple[tuple[str, tuple[Concept, ...]], ...] = (
    ("resource_download", (Concept.RESOURCE_DOWNLOAD, Concept.DOWNLOAD_STRONG, Concept.DOWNLOAD_UPDATING)),
    ("server_select", (Concept.SERVER_SELECT, Concept.SERVER_HINT, Concept.SERVER_NOT_EXIST)),
    ("login", (Concept.LOGIN, Concept.LOGIN_BUTTON, Concept.ACCOUNT_LABEL, Concept.PASSWORD_LABEL)),
)


def infer_blocked_stage(*, reason: str, ui_stage: str = "", ui_progress: str = "") -> str:
    stage = (ui_stage or "").strip()
    if stage and stage not in ("unknown", ""):
        if stage in ("resource_download", "loading"):
            return "resource_download"
        if stage in ("login", "login_form"):
            return "login"
        if stage == "sub_account_select":
            return "server_select"
        return stage

    blob = normalize_match_blob(f"{reason} {ui_progress}")

    if "stage=resource_download" in blob:
        return "resource_download"
    if "stage=login" in blob or "login_form" in blob:
        return "login"
    if "server_select" in blob:
        return "server_select"

    for canonical, concepts in _BLOB_STAGE_PRIORITY:
        if text_contains(blob, *concepts):
            return canonical

    return "unknown"
