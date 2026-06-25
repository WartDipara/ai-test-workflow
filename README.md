# android-ai-driven-test

Android 游戏自动化测试：**核心**负责 APK 预处理、LangGraph 驱动登录进游戏与交付；**GameTurbo** 为可选插件，负责加速 SDK 的 deploy、logcat 采集与 E2 失败后的 Modify 重试。

关闭插件时可只调试进游戏 UI，不部署加速 SDK。

## 架构

- **编排**：`orchestrator` 调度预处理 → 安装 → 并行 executor + observer → 交付/重试。
- **进游戏**：`graphs/` LangGraph hub 循环（`observe → classify → plan_route → 动作`）。静态 DFS（隐私/登录/选服/进游戏）优先；登录后可走 `scene` / `adaptive` / `dynamic` / `free` 旁路。阶段门禁见 `launch_phase.py`（`login_active` 时禁止误进 free/局内）。
- **LLM 分工**：`llm`（主脑）处理纯文本决策（如进游戏门 OCR 候选选点）；`llm_multimodal`（VLM）用于画面验收（`check_in_game`、网络异常、登录后 judgment 存档等），**不**负责进游戏 CTA 坐标选取。
- **登录 vs 进游戏**：凭证登录（`atomic_login`：u2 填表）与点进游戏门（`tap_enter_game`）是不同里程碑。`login_done` 以 **OCR 离开登录表单** 为准（含进入小号选择页）；VLM 仅辅助记录，不作硬失败条件。
- **进游戏门选点**：OCR 文本+坐标 → 主脑 `enter_gate_planner` 选点 → `adb tap`（无 LLM 时走启发式兜底）。
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

**GPU OCR（可选）**：`pip uninstall -y paddlepaddle && pip install paddlepaddle-gpu`，保持 `ocr.device_policy: auto`。

**设备**：`adb devices`；新设备 `python -m uiautomator2 init`。

**APK**：`apk_cache/apks.txt`

### GameTurbo（插件开启时）

1. 将 `GameTurbo-Native/` 放到项目根（与 `game_agent/` 同级）。
2. `packages/` 放 `test.jks`；`deploy.sh` 注释热更新段。
3. `games/gameturbo_{gid}.json` 新建游戏配置。

## 运行

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