"""后台多模态画面 enrichment：不阻塞 LangGraph 主路径。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from game_agent.graphs.launch_facts import merge_vision_into_facts
from game_agent.models.launch_graph_state import LaunchFacts, LaunchGraphState, facts_from_state
from game_agent.models.settings import LLMSection
from game_agent.workers.vision_worker import VisionWorker

logger = logging.getLogger(__name__)


@dataclass
class VisionEnrichmentQueue:
    """单槽异步多模态：新截图提交时取消未完成的旧任务。"""

    llm_cfg: LLMSection | None
    round_id: int = 0
    _task: asyncio.Task[str] | None = field(default=None, repr=False)
    _screenshot_path: str = field(default="", repr=False)

    def submit(
        self,
        screenshot_path: Path,
        ocr_summary: str,
    ) -> None:
        if self.llm_cfg is None:
            return
        path_str = str(screenshot_path.resolve())
        if self._task is not None and not self._task.done():
            if self._screenshot_path == path_str:
                return
            self._task.cancel()

        self._screenshot_path = path_str
        self._task = asyncio.create_task(
            self._run_vision(screenshot_path, ocr_summary),
            name="vision_enrichment",
        )

    async def _run_vision(self, screenshot_path: Path, ocr_summary: str) -> str:
        vision = VisionWorker(self.llm_cfg)  # type: ignore[arg-type]
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

    def merge_if_ready(self, state: LaunchGraphState) -> LaunchGraphState:
        state = dict(state)
        if self._task is None or not self._task.done():
            return state  # type: ignore[return-value]
        if self._task.cancelled():
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
            logger.debug(
                "[VisionEnrichment] skip stale merge task=%s current=%s",
                self._screenshot_path,
                current_shot,
            )
            return state  # type: ignore[return-value]

        facts = facts_from_state(state)
        merged = merge_vision_into_facts(facts, vision_raw)
        state["facts"] = merged.model_dump()
        state["last_vision_summary"] = vision_raw
        state["vision_enrichment_status"] = "done"
        state["pending_vision_path"] = self._screenshot_path
        logger.info(
            "[VisionEnrichment] merged vision_stage=%s",
            merged.vision_stage,
        )
        return state  # type: ignore[return-value]

    def cancel_all(self) -> None:
        """同步取消（兼容旧调用）；优先使用 shutdown()。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self._screenshot_path = ""

    async def shutdown(self) -> None:
        """等待后台多模态任务结束，避免图退出时触发 ClosedResourceError。"""
        task = self._task
        self._task = None
        self._screenshot_path = ""
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("[VisionEnrichment] shutdown absorbed: %s", e)
