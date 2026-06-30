---
name: game_launch_ocr
skill_id: game_launch_ocr
description: >-
  核心流水线：LangGraph 进游戏 + 局内 Session Agent。observe/classify/DFS、横竖屏坐标、分屏登录、OCR 填表。
  Index: skills/SKILL.md → read_repo_skill("game_launch_ocr").
---

# 核心进游戏流程（Executor / LangGraph）

**目标：** 安装后由 **LangGraph + DFS 状态树** 完成 启动 → 隐私/弹窗 → 登录 → 小号 → 选服 → 进游戏；成功须 `check_in_game` 多模态确认。点进游戏后切换 **Session Agent**（`in_game_agent`）处理教程/创角/局内引导。

**不是** 旧式主脑逐轮 `tap_and_observe` 工具链。排障看 `artifacts/.../executor/process.log`：`[LaunchGraph:route]`、`tree_trace`、`classify_reason`、`[LoginFill]`、`[ScreenCoord]`。

**与加速插件无关**：登录/UI 失败走图内 `recover_from_failure`，不按 `gameturbo.log` 判死（插件关闭时无该日志）。

---

## 主循环

```text
observe_screen (截图 + OCR + ScreenCoordSpace)
  → classify_screen (L0 facts + 可选 L2 ScreenInterpreter + vision merge)
  → plan_route (DFS / session_agent / scene / …)
  → 动作节点
  → observe_screen ...
```

`atomic_login` 成功后通常仍经 observe 刷新（键盘/黑屏检测）。

---

## 路由优先级（`launch_routing.py`）

| 条件 | 目标节点 |
|------|----------|
| DFS 静态 blocking（隐私/登录/选服等） | 对应 action |
| `session_relogin_recovery_active` | `session_relogin` |
| `session_agent_active`（已点进游戏） | `in_game_agent` |
| `scene` / `adaptive` / `dynamic` / `free` | 旁路（**session_agent 激活后 scene 不再抢路由**） |
| `login_blocking` / `vision_stage=login` 且 DFS 无动作 | `atomic_login`（避免空转 recover） |

---

## 状态树节点（DFS 顺序，节选）

| 节点 action | 触发条件 | 行为 |
|-------------|----------|------|
| `handle_initial_privacy_dialog` | 冷启动全屏隐私 | tap 同意 |
| `atomic_login` | `login_blocking` / 分屏登录 | OCR 坐标 → u2 或 **ocr-hybrid** 填表 → 提交 → OCR 验收 |
| `select_sub_account` | 小号面板 blocking | tap 配置的小号文案 |
| `handle_download` | 下载文案 | 等待/tap |
| `check_server_selector` | 选服槽 | 选服 pipeline |
| `tap_enter_game` | 进游戏 CTA | tap；成功后 `session_agent_active` |
| `in_game_agent` | 局内 Session Agent | VLM + 主脑 + OCR/脉冲（见 README 局内章节） |
| `check_in_game` | 须确认 HUD | 多模态连续确认 |
| `recover_from_failure` | 节点失败 / 兜底 | 盲重试登录、`analyze_screen`、关公告 |

里程碑：`login_done`、`sub_account_selected` 等见 `LaunchStateStore`。

---

## 横竖屏与 OCR 坐标（`screen_coord.py`）

游戏横屏但 `dumpsys mRotation=0` 时，`adb touch_size()` 可能与截图宽高比不一致，导致 OCR 缩放错误、右半屏分界偏移。

- 每轮 observe/OCR 前：`resolve_screen_coord_space(adb, screenshot)` → `deps.screen_width/height`
- 仅当 **截图与 touch 宽高比不一致** 时 swap（竖屏对齐时 **零回归**）
- 日志：`[ScreenCoord] src=2800x1260 tap=2800x1260 corrected=true`

---

## 分屏登录（横屏常见）

左侧进游戏 CTA + 右侧登录表单同时可见：

- `login_stage_probe.detect_split_screen_login` → `login_blocking=true`，`reason` 含 `split_screen_login`
- `enter_cta_visible=true` 时仍可能 `login_active`（不再被 CTA 压制）
- VLM `stage=login` + 分屏信号 → `merge_vision_into_facts` 写入 `login_blocking`

---

## 感知分层

| 层级 | 机制 | 何时 |
|------|------|------|
| L0 | OCR + `classify_screen_facts` | 每轮 classify |
| L2 | 同步 `ScreenInterpreter` | 小号无坐标、公告无 dismiss 等 |
| L1 | `NodeVerifier` OCR 差分 | 动作后验证 |
| 异步 | `VisionEnrichmentQueue` | 歧义画面后台 enrich |

OCR 坐标优先于模型坐标（`merge_interpretation_into_facts`）。

---

## 登录填表（`atomic_login` / `[LoginFill]`）

| 路径 | 条件 | 方式 |
|------|------|------|
| `u2-enter-flow` | `edits_count≥1` 且能 pick 到 EditText | u2 `setText` + Enter 跳密码 |
| `ocr-hybrid` | WebView 无节点（`edits_count=0`）或 pick 失败 | OCR tap → **IME send_keys** / light-paste |

诊断日志（`accessibility_input.py`）：

```text
[LoginFill] probe edits_count=0 account_xy=(...) password_xy=(...) screen=2800x1260
[LoginFill] route fill_path=ocr-hybrid reason=no_edits
[LoginFill] field field=username method=ime-send_keys ...
```

- 凭据：`config/credentials.yaml`
- 提交：`submit_login_after_password`（ENTER / OCR 登录按钮 / `use_cached_login_button_xy`）
- 安全键盘黑屏：observe 检测 → dismiss → 勿在黑屏上 OCR 找 Login
- 词表：`PASSWORD_HINT` 含「登录密码」等占位

---

## 并行监视（Observer）

- **AnomalyMonitor**：OCR + 多模态确认网络弹窗 → `signal_fatal`（与插件日志独立）
- **插件 LogMonitor**（仅 `gameturbo.enabled`）：采集 `gameturbo.log`，运行期不按日志 rule 判死
- 登录/UI 失败：图内 `recover_from_failure`，不走 Modify

---

## 用户中断

批跑 / Ctrl+C → `ShutdownRequested`；失败原因固定 **`用户中断操作`**，**不**触发 AI `failure_report` / Modify。

---

## 排障清单

| 症状 | 查什么 |
|------|--------|
| 横屏登录不识别 | `[ScreenCoord] corrected`；`split_screen_login`；`login_blocking` |
| WebView 不填表 | `[LoginFill] edits_count`；`method=ime-send_keys` 是否成功 |
| 小号页不点 | `sub_account_action_xy`；`sync interpret` |
| 误点进游戏 | 是否仍 `sub_account_blocking` |
| 局内卡住 | `in_game_agent_rounds`；`in_game_vlm_no_progress`；README Session Agent |
| 路由不对 | `tree_trace`、`planned_next_route`、`classify_reason` |

---

## 相关配置（settings.yaml）

```yaml
external_services:
  gameturbo:
    enabled: false   # 仅调试进游戏可关

executor:
  use_cached_login_button_xy: true
  login_submit_press_enter: true
  credential_fill_settle_s: 0.25

llm_multimodal:    # classify L2、check_in_game、局内 VLM
  ...
```

---

## 与 `plugin_accel_log` 分工

| 本 skill | 插件 skill |
|----------|------------|
| UI / LangGraph / 局内 Agent | 隧道日志健康、域名 JSON、Modify 补丁 |
| 插件可关闭 | 仅 `gameturbo.enabled: true` 时有意义 |
