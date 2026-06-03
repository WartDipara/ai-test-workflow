---
name: skills-index
description: >-
  Skill 目录：遇到问题先读本文件，再按需打开下方列出的 *_skill.md。
  勿在未读目录前直接打开长文 skill。
---

# Skill 目录（先读此文件）

## 使用顺序（执行者 / 分析 Agent 均适用）

1. **遇到问题或不确定阶段** → 调用工具 **`read_skills_index`**（读本文件）。
2. 在下方表格中匹配 **场景 / 症状** → 记下 **skill_id**。
3. 调用 **`read_repo_skill(skill_id)`** 阅读对应 `*_skill.md` 全文。
4. 若本仓库技能仍不足，且任务已成功过：可先 **`list_learned_skills`**，再 **`read_learned_skill(filename)`**（`experiences/agent_skills/`，按游戏沉淀）。

不要跳过步骤 1 直接猜流程；不要一次加载全部 skill。

---

## 仓库内置 Skill 一览

| skill_id | 文件 | 何时阅读 |
|----------|------|----------|
| `game_launch_ocr` | [game_launch_ocr_skill.md](game_launch_ocr_skill.md) | 执行者主流程：启动 → 隐私/弹窗 → 登录 → 选服 → 下载 → `check_in_game`；OCR 阶段划分、同意/下载弹窗、账号密码与安全键盘 |
| `gameturbo_log_baseline` | [gameturbo_log_baseline_skill.md](gameturbo_log_baseline_skill.md) | 分析 `gameturbo.log`、写失败报告、E2001 重试、Modify 补丁前；区分隧道重连/缓冲噪声 vs 真故障 |

---

## 与工具对应关系

| 工具 | 作用 |
|------|------|
| `read_skills_index` | 返回本目录（`skills/SKILL.md`） |
| `read_repo_skill(skill_id)` | 返回上表某一内置 skill 全文 |
| `read_login_flow_guide` | **兼容别名** → 等同 `read_repo_skill("game_launch_ocr")` |
| `list_learned_skills` / `read_learned_skill` | 本任务/历史成功 run 生成的短笔记（非通用目录） |

---

## 快速症状 → skill_id

| 症状 / 任务 | 读哪个 skill_id |
|-------------|-----------------|
| 不知道当前该点同意还是登录、下载卡住、阶段乱了 | `game_launch_ocr` |
| 账号密码、安全键盘黑屏、填表后要点登录 | `game_launch_ocr` |
| LogMonitor 报 E2001、`tunnel closed`、要不要改 direct_patterns | `gameturbo_log_baseline` |
| 写 `failure_report` / 判断加速是否正常 | `gameturbo_log_baseline` |

---

## 运行时注入（无需再读文件）

- **GameTurbo 日志基线（Retry/失败报告）**：框架在分析 prompt 中可能已注入 `gameturbo_log_baseline` 摘要；仍建议在改配置前 `read_repo_skill("gameturbo_log_baseline")`。
- **每轮短提示**：用户上下文可能带登录阶段 cheat sheet；完整规则仍以 `game_launch_ocr` 为准。
