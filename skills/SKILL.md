---
name: skills-index
description: >-
  Skill 目录：遇到问题先读本文件，再按需打开下方列出的 *_skill.md。
---

# Skill 目录（先读此文件）

## 使用顺序

1. 匹配下方 **场景 / 症状** → 记下 **skill_id**
2. 阅读对应 `*_skill.md` 全文（或 `read_repo_skill(skill_id)`）
3. Modify / 失败分析再读 `gameturbo_log_baseline`

Executor **不**在运行时加载 skill；文档供人工排障与 AI 分析 Modify 时参考。

---

## 仓库内置 Skill 一览

| skill_id | 文件 | 何时阅读 |
|----------|------|----------|
| `game_launch_ocr` | [game_launch_ocr_skill.md](game_launch_ocr_skill.md) | LangGraph 进游戏：observe/classify/DFS 节点、登录/小号/选服、OCR 与 ScreenInterpreter |
| `gameturbo_log_baseline` | [gameturbo_log_baseline_skill.md](gameturbo_log_baseline_skill.md) | 分析 gameturbo.log、失败报告、Modify 补丁、E2 重试 |

---

## 快速症状 → skill_id

| 症状 / 任务 | skill_id |
|-------------|----------|
| 不知道卡在登录还是小号、LangGraph 路由、tree_trace | `game_launch_ocr` |
| 小号页不点击、atomic_login 失败、安全键盘黑屏 | `game_launch_ocr` |
| E2001/E2002、tunnel、要不要改 direct_patterns | `gameturbo_log_baseline` |
| 写 failure_report、判断加速是否正常 | `gameturbo_log_baseline` |

---

## 工具别名（历史兼容）

| 名称 | 说明 |
|------|------|
| `read_login_flow_guide` | 等同 `read_repo_skill("game_launch_ocr")` |
