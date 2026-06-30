"""Optional external service plugins (GameTurbo, etc.) attached to the core test platform."""

from game_agent.external_services.base import (
    ExternalEvidence,
    ExternalService,
    PreparedApp,
)
from game_agent.external_services.context import ServiceContext

__all__ = [
    "ExternalEvidence",
    "ExternalService",
    "ExternalServiceManager",
    "PreparedApp",
    "ServiceContext",
]


def __getattr__(name: str):
    if name == "ExternalServiceManager":
        from game_agent.external_services.manager import ExternalServiceManager

        return ExternalServiceManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
