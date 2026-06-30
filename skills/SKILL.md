---
name: skills-index
description: >-
  Skill 目录：核心进游戏排障与可选加速插件排障分离；遇到问题先读本文件，再打开对应 *_skill.md。
---

# Skill 目录（先读此文件）

## 架构说明（2026）

本项目分为 **核心流水线** 与 **可选外部插件**，二者通过 `game_agent/external_services/manager.py` 调度，编排器不再硬编码 `GameTurbo-Native/` 路径。

| 层级 | 职责 | 相关 skill |
|------|------|------------|
| **核心** | APK 预处理、LangGraph 进游戏、局内 Session Agent、交付 | `game_launch_ocr` |
| **插件（可选）** | `external_services.gameturbo.enabled: true` 时：SDK deploy、logcat、E2 失败后 Modify 重试 | `plugin_accel_log` |

关闭插件时可只调试进游戏 UI，不部署加速 SDK（见 `README.md`）。

**运行时加载**：Executor **不**加载 skill；仅 **Modify / 失败 AI 分析**（插件开启时）通过 `read_repo_skill("plugin_accel_log")` 注入 prompt。

---

## 使用顺序

1. 匹配下方 **场景 / 症状** → 记下 **skill_id**
2. 阅读对应 `*_skill.md` 全文（或 `read_repo_skill(skill_id)`）
3. 若涉及 E2 网络重试、隧道日志、改 `direct_patterns` → 再读 `plugin_accel_log`（**仅插件开启**）

---

## 仓库内置 Skill 一览

| skill_id | 文件 | 何时阅读 |
|----------|------|----------|
| `game_launch_ocr` | [game_launch_ocr_skill.md](game_launch_ocr_skill.md) | LangGraph 进游戏：observe/classify/DFS、登录/小号/选服、横竖屏坐标、Session Agent、OCR 与 ScreenInterpreter |
| `plugin_accel_log` | [plugin_accel_log_skill.md](plugin_accel_log_skill.md) | **插件开启时**：分析 `gameturbo.log`、域名 JSON、Modify 补丁、E2 重试（与 UI 登录排障无关） |

---

## 快速症状 → skill_id

| 症状 / 任务 | skill_id |
|-------------|----------|
| LangGraph 路由、`tree_trace`、`classify_reason`、卡在登录/小号 | `game_launch_ocr` |
| 横屏分屏登录、`login_blocking` 未置位、`edits_count=0`、WebView 填表 | `game_launch_ocr` |
| 点进游戏后局内教程/创角、`in_game_agent`、VLM 无进展 | `game_launch_ocr` |
| 用户 Ctrl+C / 批跑停止（`User interrupted`） | 无需 AI skill；查 orchestrator 中断路径 |
| E2001/E2002、tunnel、`direct_patterns`、加速是否正常 | `plugin_accel_log`（插件 on） |
| 写 `failure_report`、Modify 补丁 | `plugin_accel_log`（插件 on） |

---

## 工具别名（`skill_catalog.py`）

| 调用 | 说明 |
|------|------|
| `read_repo_skill("game_launch_ocr")` | 核心进游戏 + 局内排障 |
| `read_repo_skill("plugin_accel_log")` | 插件加速日志基线（Modify prompt 使用） |
| `login` / `login_flow` | 别名 → `game_launch_ocr` |
