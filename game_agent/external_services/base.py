from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from game_agent.external_services.context import ServiceContext
    from game_agent.services.external_log_base import ExternalLogCollector


@dataclass(slots=True)
class PreparedApp:
    """Core installable application resolved for one attempt."""

    install_apk: Path
    source_apk: Path | None = None
    package_name: str = ""
    launch_activity: str = ""
    skip_install: bool = False
    prepared_by: str = "core"


@dataclass(slots=True)
class ExternalEvidence:
    """Optional plugin artifacts merged into task deliverables."""

    service_name: str
    files: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


class ExternalService(ABC):
    """Narrow plugin contract; core orchestrator never imports Native paths directly."""

    name: str

    @abstractmethod
    def is_enabled(self, ctx: ServiceContext) -> bool:
        ...

    async def prepare_installable(self, ctx: ServiceContext) -> PreparedApp | None:
        """Return prepared APK when plugin owns install preparation; None to defer to core."""
        return None

    async def before_install(self, ctx: ServiceContext, prepared: PreparedApp) -> None:
        return None

    async def after_install(self, ctx: ServiceContext, prepared: PreparedApp) -> None:
        return None

    async def before_parallel_phase(self, ctx: ServiceContext) -> None:
        return None

    async def after_parallel_phase(self, ctx: ServiceContext) -> None:
        return None

    def collect_evidence(self, ctx: ServiceContext) -> ExternalEvidence | None:
        return None

    def log_collector(self, ctx: ServiceContext) -> ExternalLogCollector | None:
        return None
