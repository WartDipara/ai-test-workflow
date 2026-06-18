from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from game_agent.external_services.context import ServiceContext
    from game_agent.models.run_failure import RunFailure


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
class RetryDecision:
    """Plugin-level retry hint after a failed attempt."""

    wants_plugin_retry: bool = False
    reason: str = ""


@dataclass(slots=True)
class ExternalEvidence:
    """Optional plugin artifacts merged into task deliverables."""

    service_name: str
    files: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


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

    async def on_failure(
        self,
        ctx: ServiceContext,
        failure: RunFailure,
        *,
        will_retry: bool,
    ) -> RetryDecision:
        return RetryDecision()

    def collect_evidence(self, ctx: ServiceContext) -> ExternalEvidence | None:
        return None

    def effective_log_monitor(self, ctx: ServiceContext, modules_log_monitor: bool) -> bool:
        """Whether parallel phase should run plugin log monitor."""
        return False

    def effective_retry_config(self, ctx: ServiceContext, modules_retry: bool) -> bool:
        """Whether failure handler may run plugin Modify/deploy retry."""
        return False
