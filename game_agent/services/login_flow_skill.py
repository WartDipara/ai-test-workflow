from __future__ import annotations

from pathlib import Path

from game_agent.paths import REPO_ROOT

LOGIN_FLOW_SKILL_PATH = REPO_ROOT / "skills" / "game-launch-ocr" / "SKILL.md"

# 每轮注入用户消息的简短阶段速查（不替代完整 SKILL）
COMPACT_STAGE_HINT = """
=== 通用登录阶段速查（各游戏 UI 不同，按 OCR 归类）===
顺序大致为：闪屏/加载 → 系统权限 → 隐私/协议 → 公告/活动 → 登录 → 选服 → 资源下载 → 进游戏。
每轮先判断当前阶段再操作；完整策略请调用 read_login_flow_guide。
阶段名：splash | system_permission | privacy | announcement | login | server_select | download | unknown
""".strip()


def read_login_flow_guide(*, max_chars: int = 24_000) -> str:
    path = LOGIN_FLOW_SKILL_PATH
    if not path.is_file():
        return f"[缺失] 未找到 {path}，请按 OCR 与工具说明自行决策。"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n…[技能文档已截断，请以当前 OCR 为准]"
    return text
