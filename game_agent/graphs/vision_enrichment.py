"""后台多模态画面 enrichment：不阻塞 LangGraph 主路径。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from game_agent.graphs.launch_facts import merge_vision_into_facts
from game_agent.models.launch_graph_state import LaunchGraphState, facts_from_state
from game_agent.models.settings import LLMSection
from game_agent.utils.ocr_util import deserialize_bboxes
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)


@dataclass
class VisionEnrichmentQueue:
    """单槽异步多模态：新截图提交时取消未完成的旧任务。"""

    llm_cfg: LLMSection | None
    round_id: int = 0
    _task: asyncio.Task[str] | None = field(default=None, repr=False)
    _screenshot_path: str = field(default="", repr=False)
    _submit_generation: int = field(default=0, repr=False)

    def submit(
        self,
        screenshot_path: Path,
        ocr_summary: str,
        *,
        attempt_context: Any | None = None,
    ) -> None:
        if self.llm_cfg is None:
            return
        path_str = str(screenshot_path.resolve())
        if self._task is not None and not self._task.done():
            if self._screenshot_path == path_str:
                return
            self._task.cancel()

        self._screenshot_path = path_str
        self._submit_generation = (
            attempt_context.get_session_generation() if attempt_context is not None else 0
        )
        self._task = asyncio.create_task(
            self._run_vision(screenshot_path, ocr_summary, attempt_context=attempt_context),
            name="vision_enrichment",
        )

    async def _run_vision(
        self,
        screenshot_path: Path,
        ocr_summary: str,
        *,
        attempt_context: Any | None = None,
    ) -> str:
        vision = VisionWorker(self.llm_cfg, attempt_context=attempt_context)  # type: ignore[arg-type]
        try:
            return await vision.analyze_game_state(
                screenshot_path=screenshot_path,
                ocr_summary=ocr_summary,
                round_id=self.round_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[VisionEnrichment] failed: %s", e)
            return ""

    def merge_if_ready(
        self,
        state: LaunchGraphState,
        *,
        attempt_context: Any | None = None,
    ) -> LaunchGraphState:
        state = dict(state)
        if self._task is None or not self._task.done():
            return state  # type: ignore[return-value]
        if self._task.cancelled():
            return state  # type: ignore[return-value]

        if attempt_context is not None:
            if attempt_context.is_session_generation_stale(self._submit_generation):
                logger.warning(
                    "[VisionEnrichment] discard stale async result gen=%d",
                    self._submit_generation,
                )
                self._task = None
                return state  # type: ignore[return-value]

        try:
            vision_raw = self._task.result()
        except Exception as e:
            logger.warning("[VisionEnrichment] result error: %s", e)
            self._task = None
            return state  # type: ignore[return-value]

        self._task = None
        if not vision_raw:
            return state  # type: ignore[return-value]

        current_shot = state.get("last_screenshot") or ""
        if self._screenshot_path and current_shot and self._screenshot_path != current_shot:
            logger.info("[VisionEnrichment] skip merge — screenshot advanced")
            return state  # type: ignore[return-value]

        facts = facts_from_state(state)
        facts = merge_vision_into_facts(
            facts,
            vision_raw,
            bboxes=deserialize_bboxes(state.get("last_bboxes")),
            screen_w=int(state.get("screen_width") or 0) or 0,
            ocr_merged=state.get("last_ocr_summary") or "",
        )
        state["facts"] = facts.model_dump()
        state["vision_enrichment_status"] = "merged"
        state["last_vision_summary"] = vision_raw[:2000]
        return state  # type: ignore[return-value]

    async def shutdown(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._task = None
