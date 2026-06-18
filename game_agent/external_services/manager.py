from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from game_agent.external_services.base import (
    ExternalEvidence,
    PreparedApp,
    RetryDecision,
)
from game_agent.external_services.gameturbo.service import GameTurboExternalService

if TYPE_CHECKING:
    from game_agent.external_services.context import ServiceContext
    from game_agent.models.run_failure import RunFailure
    from game_agent.models.settings import AppConfig

logger = logging.getLogger(__name__)


class ExternalServiceManager:
  """Registers and dispatches enabled external service plugins."""

  def __init__(self, cfg: AppConfig) -> None:
      self._cfg = cfg
      self._services = [GameTurboExternalService()]

  def gameturbo_enabled(self) -> bool:
      return bool(self._cfg.external_services.gameturbo.enabled)

  def _active(self, ctx: ServiceContext):
      for svc in self._services:
          if svc.is_enabled(ctx):
              yield svc

  async def prepare_installable(self, ctx: ServiceContext) -> PreparedApp | None:
      for svc in self._active(ctx):
          prepared = await svc.prepare_installable(ctx)
          if prepared is not None:
              logger.info(
                  "[ExternalServices] %s prepared installable apk=%s",
                  svc.name,
                  prepared.install_apk.name,
              )
              return prepared
      return None

  async def before_install(self, ctx: ServiceContext, prepared: PreparedApp) -> None:
      for svc in self._active(ctx):
          await svc.before_install(ctx, prepared)

  async def after_install(self, ctx: ServiceContext, prepared: PreparedApp) -> None:
      for svc in self._active(ctx):
          await svc.after_install(ctx, prepared)

  async def before_parallel_phase(self, ctx: ServiceContext) -> None:
      for svc in self._active(ctx):
          await svc.before_parallel_phase(ctx)

  async def after_parallel_phase(self, ctx: ServiceContext) -> None:
      for svc in self._active(ctx):
          await svc.after_parallel_phase(ctx)

  async def on_failure(
      self,
      ctx: ServiceContext,
      failure: RunFailure,
      *,
      will_retry: bool,
  ) -> RetryDecision:
      decision = RetryDecision()
      for svc in self._active(ctx):
          svc_decision = await svc.on_failure(ctx, failure, will_retry=will_retry)
          if svc_decision.wants_plugin_retry:
              decision = svc_decision
      return decision

  def collect_all_evidence(self, ctx: ServiceContext) -> dict[str, ExternalEvidence]:
      out: dict[str, ExternalEvidence] = {}
      for svc in self._active(ctx):
          evidence = svc.collect_evidence(ctx)
          if evidence is not None:
              out[svc.name] = evidence
      return out

  def effective_log_monitor(self, ctx: ServiceContext) -> bool:
      if not ctx.app_config.modules.log_monitor:
          return False
      return self.gameturbo_enabled()

  def effective_retry_config(self, ctx: ServiceContext) -> bool:
      if not ctx.app_config.modules.retry_on_failure:
          return False
      return self.gameturbo_enabled()
