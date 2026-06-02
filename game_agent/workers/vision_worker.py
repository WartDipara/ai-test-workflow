from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryImage

from game_agent.models.game_entry_judgment import GameEntryJudgment
from game_agent.models.settings import LLMSection
from game_agent.models.worker_task import WorkerProgress, WorkerTaskResult
from game_agent.services.llm_service import build_llm_model

logger = logging.getLogger(__name__)


class VisionWorker:
    """多模态视觉职员：只负责观察、分析、汇报，不直接执行设备操作。"""

    def __init__(self, llm_config: LLMSection) -> None:
        self._llm_config = llm_config
        self._agent = Agent(build_llm_model(llm_config), output_type=str)

    async def analyze_game_state(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        round_id: int | None = None,
    ) -> str:
        """
        用于监控游戏运行状态（如下载、登录、进入游戏）及异常情况。
        返回一段 JSON 格式的字符串，包含 status、stage、message 等字段。
        """
        prompt = f"""
请分析当前游戏画面的状态和是否有异常。
画面 OCR 结果：
{ocr_summary}

任务：
1. 识别当前所处阶段：
   - resource_download: 正在下载资源（进度条）
   - login: 登录界面（服务器列表等）
   - enter_game: 已进入游戏或正在连接游戏服
   - unknown: 未知状态
2. 识别是否有【网络相关】异常情况发生：
   - 仅当画面出现以下网络类错误弹窗/提示时，才判定 has_anomaly=true：
     * “网络连接失败”、“网络异常”、“网络无连接”、“没有网络”、“请检查网络”
     * “连接超时”、“连接失败”、“服务器连接失败”、“与服务器断开连接”
     * “服务器加载失败”、“服务器获取失败”、“服务器繁忙”、“服务器维护中”
     * “资源下载失败”、“资源加载失败”、“更新失败”、“下载失败”
     * “当前地区不支持”、“当前区域暂未开放”
   - 【重要】以下情况 NOT 视为异常，必须 has_anomaly=false：
     * “账号或密码错误”、“登录失败”、“验证码错误”、“账号异常”、“账号被冻结”
     * 任何与账号、密码、验证、实名认证相关的错误提示
     * 服务器选择界面、排队等待、加载中（正常流程）
     * “同意协议”、“公告”、“活动弹窗”等正常运营内容
3. 如果是在下载阶段，尝试提取当前进度百分比。

请严格返回合法的 JSON 对象，不要输出 markdown code block，直接输出 JSON 文本：
{{
    "has_anomaly": bool,
    "anomaly_reason": "如果有异常，写明原因；如果没有，为空",
    "stage": "resource_download | login | enter_game | unknown",
    "progress": "如果在下载阶段，提取到的进度（如 '45%'），否则为空"
}}
"""
        prefix = f"[VisionWorker] 第 {round_id} 轮" if round_id is not None else "[VisionWorker]"
        model = self._llm_config.model_name
        logger.info(
            "%s 请求多模态 API | model=%s | 截图=%s",
            prefix,
            model,
            screenshot_path.name,
        )
        t0 = time.perf_counter()
        try:
            result = await self._agent.run([prompt, BinaryImage.from_path(screenshot_path)])
            output = (result.output or "").strip()
            elapsed = time.perf_counter() - t0
            preview = output.replace("\n", " ")[:240]
            logger.info(
                "%s API 返回 | 耗时 %.2fs | 输出预览: %s%s",
                prefix,
                elapsed,
                preview,
                "..." if len(output) > 240 else "",
            )
            return output
        except Exception:
            elapsed = time.perf_counter() - t0
            logger.exception("%s API 失败 | 耗时 %.2fs", prefix, elapsed)
            return '{"has_anomaly": false, "anomaly_reason": "", "stage": "unknown", "progress": ""}'

    async def judge_in_game_main(
        self,
        *,
        screenshot_path: Path,
        ocr_summary: str,
        ocr_creation_hits: list[str] | None = None,
        round_id: int | None = None,
        session_index: int = 1,
        sessions_restarted: int = 0,
    ) -> GameEntryJudgment:
        """
        独立判定：是否已进入游戏内（登录完成、局内场景；含强制新手引导蒙层也算进入）。
        不参考按键精灵脚本。创角相关 OCR 命中时必须在 blockers 含 character_creation。
        """
        creation_block = ""
        if ocr_creation_hits:
            creation_block = (
                "\n【硬性提示】OCR 已命中创角/局外关键词: "
                + ", ".join(ocr_creation_hits)
                + "。除非画面明确证明已离开创角流程，否则 in_game_main 必须为 false，"
                "blockers 须包含 character_creation。\n"
            )

        session_block = ""
        if sessions_restarted > 0:
            session_block = (
                f"\n【会话上下文】当前为第 {session_index} 次游戏会话，"
                f"此前已发生 {sessions_restarted} 次游戏 crash/重启。"
                "请仅根据本张截图判断阶段，勿沿用任何历史截图结论；"
                "重启后常见为 resource_download 或 login。\n"
            )

        prompt = f"""
你是游戏自动化测试中的「进入游戏」判定器。只根据截图与 OCR 判断：玩家是否已经进入游戏内可玩场景。
不要参考任何外部脚本、找色配置或按键精灵逻辑。

画面 OCR（坐标+文字+置信度）：
{ocr_summary}
{creation_block}{session_block}

## 判为 in_game_main=true（已进入游戏内）的条件
- 已离开登录/注册/选服/协议等局外界面；
- 已离开资源下载进度条为主的界面；
- 已离开「连接中/加载中」等纯过渡画面；
- 已离开创建角色流程（选职业、取名、捏脸等）；
- 当前为游戏内 3D/2D 场景或游戏 HUD；**即使存在强制新手引导、全屏蒙层、手指指引、剧情对话框，仍算已进入游戏**（stage 可为 tutorial_overlay，但 in_game_main 仍为 true）。

## 判为 in_game_main=false 的情况
- 仍在登录、选服、下载资源、创角、仅显示桌面/启动器；
- OCR 含创角相关词且画面仍是创角界面。

## stage 枚举
login | server_select | resource_download | loading | character_creation | tutorial_overlay | in_game_main | unknown

请严格输出合法 JSON（不要 markdown 代码块）：
{{
    "in_game_main": bool,
    "confidence": 0.0到1.0,
    "stage": "上述枚举之一",
    "ocr_signals": ["你依据的关键 OCR 片段"],
    "reason": "一句话理由",
    "blockers": ["如 character_creation、login_screen，无则 []"]
}}
"""
        prefix = (
            f"[VisionWorker:game_entry] 第 {round_id} 轮"
            if round_id is not None
            else "[VisionWorker:game_entry]"
        )
        model = self._llm_config.model_name
        logger.info(
            "%s 进入游戏判定 | model=%s | 截图=%s",
            prefix,
            model,
            screenshot_path.name,
        )
        t0 = time.perf_counter()
        try:
            result = await self._agent.run([prompt, BinaryImage.from_path(screenshot_path)])
            raw = (result.output or "").strip()
            elapsed = time.perf_counter() - t0
            judgment = _parse_game_entry_judgment(raw)
            logger.info(
                "%s 判定 | %.2fs | in_game=%s conf=%.2f stage=%s | %s",
                prefix,
                elapsed,
                judgment.in_game_main,
                judgment.confidence,
                judgment.stage,
                judgment.reason[:200],
            )
            if ocr_creation_hits and judgment.in_game_main:
                judgment = judgment.model_copy(
                    update={
                        "in_game_main": False,
                        "blockers": list(
                            dict.fromkeys(
                                [*judgment.blockers, "character_creation"],
                            ),
                        ),
                        "reason": (
                            f"OCR 创角词覆盖: {ocr_creation_hits}; "
                            + judgment.reason
                        )[:500],
                    },
                )
            return judgment
        except Exception:
            elapsed = time.perf_counter() - t0
            logger.exception("%s API 失败 | %.2fs", prefix, elapsed)
            return GameEntryJudgment(
                in_game_main=False,
                confidence=0.0,
                stage="unknown",
                reason="多模态 API 调用失败",
            )

    async def analyze_screen(
        self,
        *,
        task_id: str,
        round_id: int,
        screenshot_path: Path,
        ocr_summary: str,
        foreground: str,
        screen_size: tuple[int, int],
        mission_context: str,
        report_progress: Callable[[WorkerProgress], None],
    ) -> WorkerTaskResult:
        report_progress(
            WorkerProgress(
                status="running",
                progress=15,
                current_step="compose_prompt",
                message="视觉职员正在整理截图、OCR 结果与前台应用上下文",
            ),
        )
        prompt = _build_prompt(
            task_id=task_id,
            round_id=round_id,
            ocr_summary=ocr_summary,
            foreground=foreground,
            screen_size=screen_size,
            screenshot_path=screenshot_path,
            mission_context=mission_context,
        )
        report_progress(
            WorkerProgress(
                status="running",
                progress=35,
                current_step="request_sent",
                message="视觉职员已向多模态模型提交截图分析请求",
            ),
        )
        try:
            result = await self._agent.run([prompt, BinaryImage.from_path(screenshot_path)])
        except Exception:
            logger.exception("vision worker failed: task_id=%s", task_id)
            raise
        report = (result.output or "").strip()
        report_progress(
            WorkerProgress(
                status="reporting",
                progress=90,
                current_step="format_report",
                message="视觉职员已收到模型输出，正在整理最终报告",
            ),
        )
        if not report:
            report = "视觉职员未返回有效内容，请主脑结合 OCR 结果保守决策。"
        return WorkerTaskResult(
            report=report,
            metadata={
                "round_id": round_id,
                "screenshot_path": str(screenshot_path),
                "foreground": foreground,
            },
        )


def _build_prompt(
    *,
    task_id: str,
    round_id: int,
    ocr_summary: str,
    foreground: str,
    screen_size: tuple[int, int],
    screenshot_path: Path,
    mission_context: str,
) -> str:
    width, height = screen_size
    return f"""
你是多 Agent 协作系统里的视觉职员 Agent B。
你的身份不是决策者，也不能直接操作设备；你的职责是观察截图、提取信息，并向主脑 Agent A 汇报。

任务元数据：
- task_id: {task_id}
- round_id: {round_id}
- screenshot_path: {screenshot_path}
- screen_size: {width}x{height}
- foreground: {foreground}

当前任务上下文：
{mission_context}

当前界面的 OCR 识别结果（每条为「中心坐标 + 文字 + 置信度」，用于定位列表项与按钮）：
{ocr_summary}

请按以下结构汇报，避免输出无关解释：

1. 当前页面类型：
   在 keywizard_ad / keywizard_home / script_editor_entry / script_category_list / script_list / script_detail / root_permission / loading / unknown 中选择最接近的一项，并说明依据。

2. 关键文本：
   列出画面中可见的按钮、提示、广告文字、分类名、脚本名、加载/启动状态等。能推断位置时写出大致坐标。

3. 关键交互目标：
   列出主脑接下来可能要点击或输入的目标。每项包含：
   - name
   - suggested_action，例如 tap_coordinate(x,y)、tap_and_observe(x,y)、swipe_screen(direction)、wait_seconds
   - reason
   - confidence，0 到 1
   如果 foreground 不是目标按键精灵包，或当前明显在桌面/启动器，请优先建议主脑调用 open_keywizard_app，而不是建议点击桌面图标坐标。

4. 风险与不确定性：
   如果坐标不确定、截图过渡中、OCR 与画面冲突，请明确说明。

5. 给主脑的简短建议：
    用 1-3 句话说明下一步最稳妥的动作。
""".strip()


def _parse_game_entry_judgment(raw: str) -> GameEntryJudgment:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return GameEntryJudgment.model_validate(json.loads(text))
    except Exception:
        return GameEntryJudgment(
            in_game_main=False,
            confidence=0.0,
            stage="unknown",
            reason=f"无法解析模型 JSON: {text[:300]}",
        )
