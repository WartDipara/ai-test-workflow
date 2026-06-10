from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ServerSlotStatus = Literal["empty", "loading", "ready", "error", "not_visible"]
ServerProbeRecommendation = Literal["tap_verify", "fail_fast", "wrong_stage"]


class ServerConnectivityProbe(BaseModel):
    on_enter_game_screen: bool = False
    enter_button_visible: bool = False
    server_slot_status: ServerSlotStatus = "not_visible"
    server_list_likely_available: bool = False
    has_network_error_ui: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    recommendation: ServerProbeRecommendation = "tap_verify"
