---
name: game_launch_ocr
skill_id: game_launch_ocr
description: >-
  LangGraph 进游戏流程：observe → classify → DFS 节点。Index: skills/SKILL.md → read_repo_skill("game_launch_ocr").
---

# LangGraph 进游戏流程（Executor）

**目标：** deploy 后由 **LangGraph + DFS 状态树** 自动完成 启动 → 隐私/弹窗 → 登录 → 小号 → 选服 → 进游戏；成功须 `check_in_game` 多模态确认。

**不是** 主脑 Agent 逐轮调 `tap_and_observe` / `fill_credential_field` 工具。排障请看 `artifacts/.../executor/process.log` 中 `[LaunchGraph:route]`、`tree_trace`、`classify_reason`。

---

## 主循环

```text
observe_screen (截图 + OCR)
  → classify_screen (L0 正则 facts + 必要时 L2 ScreenInterpreter)
  → plan_route (DFS 选下一节点)
  → 动作节点
  → observe_screen ...
```

`atomic_login` 成功后跳过 observe，直接 `classify_screen`。

---

## 状态树节点（DFS 顺序）

| 节点 action | 触发条件（guard） | 行为 |
|-------------|-------------------|------|
| `handle_initial_privacy_dialog` | 冷启动全屏隐私弹窗 | tap 同意按钮 |
| `atomic_login` | 登录页 blocking | OCR → u2 填账号密码 → 提交 → OCR 验收 |
| `select_sub_account` | 小号面板 blocking | tap 已有小号；无坐标时 Interpreter 补 `tap_target`；OCR 差分验证 |
| `handle_download` | 下载文案可见 | tap 继续/确定或等待 |
| `ensure_privacy_checkbox` | 登录页协议 checkbox | 多模态 + MolmoPoint 勾选 |
| `check_server_selector` | 选服槽可见 | 完整选服 pipeline |
| `tap_enter_game` | 踏入仙途等 CTA | tap 进游戏按钮 |
| `check_in_game` | 已点进游戏且无 CTA | 多模态连续确认 |
| `recover_from_failure` | 无可用节点 / 节点失败 | 盲重试登录、analyze_screen、关公告 |

里程碑写入 `LaunchStateStore`（`login_done`、`sub_account_selected` 等），节点记录用 tree node id（如 `login.select_sub_account`）。

---

## 感知分层

| 层级 | 机制 | 何时 |
|------|------|------|
| L0 | OCR + 正则 `classify_screen_facts` | 每轮 classify |
| L2 | 同步 `ScreenInterpreter`（多模态 JSON） | 小号无坐标、公告无 dismiss 坐标、创角 hint 等 |
| L1 | `NodeVerifier` OCR 文本差分 | 动作后验证（如小号页离开） |
| 异步 | `VisionEnrichmentQueue` + `analyze_game_state` | 下载/歧义画面后台 enrich |

OCR 坐标优先于模型坐标（`merge_interpretation_into_facts`）。

---

## 阶段与 facts 字段

| current_stage | 主要 facts | 说明 |
|---------------|------------|------|
| `privacy` | `initial_privacy_dialog` | 全屏协议，先于登录 |
| `login_form` | `login_blocking` | `atomic_login` |
| `sub_account_select` | `sub_account_blocking`, `sub_account_action_xy` | 勿点背景踏入仙途 |
| `server_select` | `server_slot_visible`, `enter_cta_visible` | 先 `check_server_selector` |
| `download` | `download_visible` | 等待为主，Anomaly 监视停滞 |
| 创角（未专节点） | `character_creation_blocking` | 阻塞 tap/check_in_game，走 recover |

凭据：`config/credentials.yaml`，由 `atomic_login` 读取，不经过 LLM。

---

## 登录与安全键盘

- 填表：u2 `setText` + 可选无障碍校验（`executor.credential_*`）
- 提交：ENTER → u2 Login → **OCR 阶段缓存的按钮坐标**（`use_cached_login_button_xy`）
- 黑屏：observe 检测 secure keyboard → 点空白收起 → 勿在收键盘后再 OCR 找 Login
- 验收：`verify_login_with_ocr` / `probe_login_stage`

---

## 并行监视（Observer）

- **LogMonitor**：只采集 `gameturbo.log`，运行期不按日志判死
- **AnomalyMonitor**：独立 OCR 轮询 + 多模态确认网络异常 → `signal_fatal`
- 登录/UI 失败在图内 `recover_from_failure`，不走 region 重试

---

## 排障清单

| 症状 | 查什么 |
|------|--------|
| 小号页一直 OCR 不点 | `sub_account_action_xy` 是否空；`sync interpret` 日志；`failed_nodes.login.select_sub_account` |
| 登录循环 | `atomic_login` failed reason；安全键盘黑屏；`login_done` flag |
| 误点进游戏 | 是否仍在 `sub_account_blocking` / `login_blocking` |
| 创角卡住 | `character_creation_blocking`；尚无专用节点，依赖 recover + Interpreter |
| 路由不对 | `tree_trace`、`planned_next_route`、`classify_reason` |

---

## 相关配置（settings.yaml）

```yaml
executor:
  post_launch_wait_s: 2.0
  use_cached_login_button_xy: true
  login_submit_press_enter: true
  credential_verify_after_fill: true

llm_multimodal:  # classify L2、check_in_game、recover 必需
  ...
```

---

## 与 gameturbo_log_baseline 分工

- **本 skill**：Executor UI / LangGraph 阶段
- **gameturbo_log_baseline**：日志健康、Modify 补丁、E2 重试（运行期不读日志判死）
