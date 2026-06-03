from __future__ import annotations

import re
from typing import TYPE_CHECKING

from game_agent.models.run_state import RunState
from game_agent.services.login_flow_skill import COMPACT_STAGE_HINT

if TYPE_CHECKING:
    from game_agent.models.settings import AppConfig
    from game_agent.modules.run_context import AttemptContext

# 记忆策略（与产品优先级一致）：
# 1. AI 分析正确 — 完整 message_history + 任务锚点 + 工具链 action log + 跨 retry 摘要
# 2. 响应速度 — 不裁剪 history；仅减少每轮重复的静态 hint（history 仍保留首轮完整 skill 摘要）
# 3. 上下文体积 — 最多 3 次尝试，默认不压缩 API history

_STAGE_RE = re.compile(
    r"\bstage(?:\s*id)?\s*[:=]\s*[`'\"]?([a-z][a-z0-9_]*)\b",
    re.IGNORECASE,
)


def extract_declared_stage(model_output: str) -> str | None:
    """从执行者本轮文本输出解析 stage ID（供下轮锚定，不替代 history）。"""
    if not model_output:
        return None
    m = _STAGE_RE.search(model_output)
    if not m:
        return None
    return m.group(1).lower().strip()[:64]


def format_milestones(run_state: RunState) -> str:
    parts: list[str] = []
    if run_state.package_install_confirmed:
        parts.append("package_installed")
    if run_state.game_started:
        parts.append("process_up")
    if run_state.launch_wait_invoked:
        parts.append("wait_for_game_running_called")
    if run_state.in_game_confirm_streak:
        parts.append(f"in_game_streak={run_state.in_game_confirm_streak}")
    if run_state.in_game_confirmed:
        parts.append("in_game_confirmed")
    if run_state.finished:
        parts.append(f"flow_finished(success={run_state.success})")
    return ", ".join(parts) if parts else "(none yet)"


def build_mission_anchor(
    *,
    package_name: str,
    main_screen_confirm_rounds: int,
    attempt_index: int = 1,
    max_attempts: int = 1,
) -> str:
    return (
        "=== Mission anchor (read every round; do not drop this goal) ===\n"
        f"Goal: launch `{package_name}` → complete login/server/download → in-game.\n"
        "Tools: OCR + tap/swipe; multimodal only via analyze_screen / check_in_game (JSON errorCode).\n"
        f"Success: check_in_game data.confirmed=true after {main_screen_confirm_rounds} "
        "consecutive positives (errorCode=0). wait_for_game_running is process-only.\n"
        "Popups: prefer Agree/Accept/确认/继续/下载; handle privacy on first launch; "
        "confirm download-size dialogs then wait.\n"
        "Each reply: current stage ID + concrete next tool(s).\n"
        f"Pipeline attempt {attempt_index}/{max_attempts} "
        "(full conversation history is preserved for this run).\n"
        "On retry after GameTurbo config patch: verify the last blocked stage "
        "(often download/update) is passed before check_in_game."
    )


def build_prior_attempt_block(prior_brief: str) -> str:
    brief = (prior_brief or "").strip()
    if not brief:
        return ""
    return (
        "=== Prior pipeline attempt (facts only; continue from deploy state) ===\n"
        + brief[:2500]
    )


def should_include_compact_stage_hint(round_id: int, every_n_rounds: int) -> bool:
    """首轮必带；之后按间隔附带（完整 skill：read_skills_index → read_repo_skill）。"""
    if round_id == 0:
        return True
    if every_n_rounds <= 0:
        return False
    return (round_id + 1) % every_n_rounds == 0


def build_executor_user_parts(
    *,
    cfg: AppConfig,
    round_id: int,
    max_rounds: int,
    screen_w: int,
    screen_h: int,
    target_pkg: str,
    fg_line: str,
    ocr_summary: str,
    run_state: RunState,
    session_action_log: str,
    attempt_context: AttemptContext | None,
    not_foreground_rounds: int,
    cred_hint: str,
) -> list[str]:
    """组装单轮 user 消息分块；不修改 message_history。"""
    ag = cfg.agent
    actx = attempt_context
    attempt_index = actx.attempt_index if actx is not None else 1
    max_attempts = actx.max_attempts if actx is not None else 1
    prior_brief = actx.prior_attempt_brief if actx is not None else ""

    parts: list[str] = [
        build_mission_anchor(
            package_name=target_pkg,
            main_screen_confirm_rounds=cfg.game.main_screen_confirm_rounds,
            attempt_index=attempt_index,
            max_attempts=max_attempts,
        ),
    ]

    prior_block = build_prior_attempt_block(prior_brief)
    if prior_block:
        parts.append(prior_block)

    if should_include_compact_stage_hint(round_id, ag.repeat_compact_stage_hint_every_n_rounds):
        parts.append(COMPACT_STAGE_HINT)

    dynamic = (
        f"Round {round_id + 1}/{max_rounds}. Screen={screen_w}x{screen_h}. "
        f"Foreground={fg_line}. "
        f"Non-game foreground streak={not_foreground_rounds}. "
        f"Milestones: {format_milestones(run_state)}. "
    )
    if run_state.last_declared_stage:
        dynamic += f"Last declared stage (prior round): {run_state.last_declared_stage}. "
    if run_state.round_hint:
        dynamic += f"System hint: {run_state.round_hint[:500]}. "
    dynamic += (
        f"Suggested ad/load wait={cfg.executor.ad_initial_wait_s:.1f}s. "
        f"Launch detect timeout={cfg.game.launch_detect_timeout_s:.0f}s. "
        f"Credentials: {cred_hint}"
    )
    if round_id == 0 and not run_state.package_install_confirmed:
        dynamic += (
            " Post-deploy: call wait_for_package_installed ONCE, then open_game_app."
        )
    elif round_id == 0 and run_state.package_install_confirmed:
        dynamic += (
            " Package already on device (verified after deploy). "
            "Skip wait_for_package_installed; call open_game_app then get_ocr_summary."
        )
    if actx is not None:
        fatal = actx.get_fatal_reason()
        if fatal:
            dynamic += f" FATAL from monitor: {fatal[:200]}."
        else:
            dynamic += f" {actx.format_observer_hint()}."
    if run_state.game_started:
        dynamic += (
            f" Process up. in_game streak={run_state.in_game_confirm_streak}/"
            f"{cfg.game.main_screen_confirm_rounds}."
        )
    parts.append(dynamic)

    parts.append(
        "=== Action log (system, full run tool chain) ===\n" + session_action_log,
    )
    parts.append(
        "=== Foreground (dumpsys) ===\n"
        f"foreground={fg_line}\n"
        f"target_package={target_pkg}\n"
        f"target_activity={cfg.game.launch_activity}\n",
    )
    parts.append(
        f"=== Screen OCR (round {round_id + 1} opening snapshot) ===\n"
        "Stale after tap in this round — use get_ocr_summary or tap_and_observe.\n"
        + (ocr_summary or "")[:8000],
    )
    return parts
