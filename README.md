# android-ai-driven-test

Android 游戏自动化测试：**核心**负责 APK 预处理、LangGraph 驱动登录进游戏与交付；**GameTurbo** 为可选插件，负责加速 SDK 的 deploy、logcat 采集与 E2 失败后的 Modify 重试。

关闭插件时可只调试进游戏 UI，不部署加速 SDK。

## 架构

- **编排**：`orchestrator` 调度预处理 → 安装 → 并行 executor + observer → 交付/重试。
- **进游戏**：`graphs/` LangGraph hub 循环（`observe → classify → plan_route → 动作`）。静态 DFS（隐私/登录/选服/点进入）优先；**点击「进入游戏」后**切换 **Session Agent**（`in_game_agent`：主脑 + VLM + OCR/脉冲工具），创角/教程/战前布阵均由 Agent 自主决策。进入前可选 `scene` / `adaptive` / `dynamic` / `free` 旁路；`session_agent_active` 后 `scene_action` 与 `scene_gate` 路由不再抢控制权。阶段门禁见 `launch_phase.py`。
- **LLM 分工**：`llm`（主脑）处理纯文本决策（进游戏门选点、局内行为链规划等）；`llm_multimodal`（VLM）用于画面验收（`check_in_game`、网络异常、**局内画面语义分析**等）。进游戏 CTA 坐标仍由 OCR + 主脑 `enter_gate_planner` 选取，不走 VLM 直接给像素。
- **登录 vs 进游戏**：凭证登录（`atomic_login`：u2 填表）与点进游戏门（`tap_enter_game`）是不同里程碑。`login_done` 以 **OCR 离开登录表单** 为准（含进入小号选择页）；VLM 仅辅助记录，不作硬失败条件。
- **进游戏门选点**：OCR 文本+坐标 → 主脑 `enter_gate_planner` 选点 → `adb tap`（无 LLM 时走启发式兜底）。
- **小号选择**：`credentials.yaml` 可配置 `sub_account`（如 `小号1` / `Sub-account 1`），OCR 按配置精确匹配行点击，避免误点「小号说明」等说明文案。
- **登录前弹窗**：公告/遮罩关闭失败若误触「退出游戏」确认框，观察者轮次会自动 OCR 点「取消」再继续。
- **插件**：`external_services/manager` 调度 `gameturbo/`，编排器不直接依赖 `GameTurbo-Native/` 路径。契约见 `external_services/base.py`。

## 环境要求


| 依赖               | 要求                                                                              |
| ---------------- | ------------------------------------------------------------------------------- |
| Python           | 3.12.x                                                                          |
| ADB / aapt       | PATH 可用，`adb devices` 可见设备                                                      |
| Git Bash         | Windows 下跑 `deploy.sh` / `run.sh`                                               |
| LLM              | 主脑 `llm`（进游戏门选点等文本决策）+ 多模态 `llm_multimodal`（`check_in_game` 等验收；executor 开启时必填） |
| GameTurbo-Native | 仅插件 `enabled: true` 时需要；不在 git 中，需单独获取                                          |


## 初始化

```bash
git clone <repo> android-ai-driven-test && cd android-ai-driven-test
python3.12 -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -U pip && pip install -e ".[dev]"
cp config/settings.example.yaml config/settings.yaml
cp config/credentials.example.yaml config/credentials.yaml
```

**settings.yaml 要点**：`llm` / `llm_multimodal` API；`external_services.gameturbo.enabled`；单设备可填 `adb.serial`，批跑忽略。

**局内相关配置**（`game.*`）：

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `in_game_vlm_no_progress_fail_rounds` | 10 | 连续 VLM 判定无画面进展的动作轮次上限，达到后强制失败 |
| `in_game_vlm_progress_min_confidence` | 0.55 | 局内进展 judge 最低置信度 |
| `in_game_post_action_vlm_analyze` | true | 行为执行后是否调用 VLM 判断进展（可关以省成本） |
| `motion_probe.*` | 见 example | OpenCV 连拍脉冲（Session Agent / `in_game_agent` 阶段） |

**凭据**：`credentials.yaml` 除账号密码外，可选 `sub_account` 指定要点击的小号文案。

**GPU OCR（可选）**：`pip uninstall -y paddlepaddle && pip install paddlepaddle-gpu`，保持 `ocr.device_policy: auto`。

**设备**：`adb devices`；新设备 `python -m uiautomator2 init`。

**APK**：`apk_cache/apks.txt`

### GameTurbo（插件开启时）

1. 将 `GameTurbo-Native/` 放到项目根（与 `game_agent/` 同级）。
2. `packages/` 放 `test.jks`；`deploy.sh` 注释热更新段。
3. `games/gameturbo_{gid}.json` 新建游戏配置。

## 运行

**Windows**：请使用 **Git Bash** 执行 `./run.sh`（deploy.sh 依赖 bash）。若 PATH 中无 bash，可在 `settings.yaml` 的 `gameturbo.bash_path` 指定，例如 `C:/Program Files/Git/bin/bash.exe`。

```bash
./run.sh
# 或: python -m game_agent.main
```


| 退出码 | 含义                       |
| --- | ------------------------ |
| 0   | 成功（须 `check_in_game` 确认） |
| 1   | 失败                       |
| 2   | 配置错误                     |
| 130 | Ctrl+C                   |


批跑：`apks.txt` 每条 URL 一任务，多设备自动认领。产出 `run_outputs/{gid}_{task_id}/`。

## 流水线

预处理 → 安装（插件：bootstrap + deploy；关闭插件：源 APK）→ 并行 **executor**+ **observer**（logcat 采集 + 网络异常 OCR/VLM）→ 成功交付，或 E2 失败走 cleanup → modify → 再 deploy。

**成功**：并行阶段无错，且 `check_in_game` 多轮 VLM 确认。deploy 完成不算成功。

## Session Agent / 局内新手引导（`in_game_agent`）

**点击「进入游戏」**（`tap_enter_game` 或等价入口）后置 `session_agent_active`，路由优先进入 **`in_game_agent`**，不再走刚性 `scene_action`（对话 OCR 点气泡等）。创角、教程上阵、战前布阵与局内 HUD 推进共用同一 Agent 循环：**VLM 画面分析 + 主脑决策 + OCR/脉冲坐标工具**，直至教程完成或失败熔断。

### 三层决策路由（简单快、复杂慢）

局内每轮 `in_game_agent` 按优先级尝试，命中则跳过后续层：

| 层级 | 模块 | 适用场景 | 跳过 |
|------|------|----------|------|
| 1 经验记忆 | `scene_memory_runner` | 已学过的对话/空白继续/技法选择 | VLM + 主脑 |
| 2 OCR 启发式 | `in_game_heuristic_fast_path` | 纯对话推进、空白继续（无脉冲/卡牌引导） | VLM + 主脑 |
| 3 慢路径 | VLM analyze + 主脑 + 可选 motion burst | 战斗必杀、点卡牌、复杂教程 | — |

战斗/脉冲类场景（如「点我放必杀」、`recommended_coord_source=pulse`、forced_guidance）**不走** 1–2 层快路径，由 `should_run_motion_burst` 软门控开启 OpenCV 连拍。配置：`game.in_game_fast_path_enabled`（默认 true）、`motion_probe.burst_on_forced_guidance` / `burst_on_no_progress`。

### 分工：VLM 语义 → 主脑选源 → 坐标解析

```
截图 + OCR + OpenCV 脉冲
    → VLM analyze_in_game_screen（画面语义、目标描述、是否有 OCR 文案）
    → 主脑 decide_in_game_session_round（verdict + 行为链，每步带 coord_source）
    → TapCoordResolver.resolve_step_coordinates（按源绑定像素）
    → execute_behavior_step
```

| `coord_source` | 适用场景 | 坐标来源 |
|----------------|----------|----------|
| `ocr` | 按钮/卡牌有可读 OCR（如「战斗」） | `bbox_for_text_strict` 精确/最长匹配，可用 VLM `tap_x/y` 消歧 |
| `vlm_xy` | VLM `motion_ocr_fused` 已给出可靠坐标 | **保留 VLM 坐标**，禁止单子串 OCR 覆盖 |
| `pulse` | 教程「点击卡牌」等目标无 OCR、有脉冲/手指 | OpenCV 脉冲 + 可选 `judge_tutorial_pulse` |
| `dialogue_blank` | 「点击空白继续」类对话 | 暗色区域 / 对话空白启发 |

**互补原则**：OCR 与脉冲不互相覆盖。有文字语义走 OCR（完整文案，如「战斗」而非单字「战」）；无文字但有强指引走脉冲。VLM 输出 `target_has_ocr_semantics`、`semantic_target_text`、`recommended_coord_source` 供主脑决策。

**无进展硬顶**：非 `wait` 动作执行后，VLM judge 判定画面是否推进；连续 `in_game_vlm_no_progress_fail_rounds`（默认 10）次无进展则强制失败，与主脑主动 `verdict=fail` 并存。

**关键模块**：`in_game_screen_analyze.py`、`in_game_session_planner.py`、`tap_coord_resolver.py`、`tutorial_pulse_locator.py`、`in_game_progress_judge.py`。

**观测**：运行期不按 logcat 规则判死；网络异常由 AnomalyMonitor 截图 + 多模态确认后 fatal。登录/UI 失败在图内 `recover_from_failure`。

阶段日志前缀见 `process.log`。插件开启时有 `gameturbo.log`、`final_logs.log`。

## 失败与重试


| 区间    | 含义                         | 可重试                   |
| ----- | -------------------------- | --------------------- |
| E1xxx | 配置/deploy/登录/UI/Modify 硬失败 | 否                     |
| E2xxx | 网络/加速类（OCR+多模态确认）          | 是（`retry_on_failure`） |


常见码：E1009 deploy 未安装；E1010 超时未进游戏；E2002 网络画面异常。详情见 `models/run_failure.py`。

Modify 参考 `domain_region_analysis.json`、`anomaly_evidence.json`、`gameturbo.log`（插件产出）。

## 常见问题

**只调试进游戏？** `external_services.gameturbo.enabled: false`，保留 `modules.executor`、`llm`与 `llm_multimodal`。