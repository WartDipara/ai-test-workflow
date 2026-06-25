"""GameTurbo external plugin — wraps Native bootstrap, deploy, and log collection."""

from game_agent.external_services.gameturbo.service import GameTurboExternalService
from game_agent.external_services.gameturbo.log import (
    GAMETURBO_LOG_COLLECTOR,
    GameTurboLogCollector,
)

__all__ = [
    "GAMETURBO_LOG_COLLECTOR",
    "GameTurboExternalService",
    "GameTurboLogCollector",
]
