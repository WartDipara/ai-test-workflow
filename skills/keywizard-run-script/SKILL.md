---
name: keywizard-run-script
description: >-
  项目内按键精灵脚本运行规范：写脚本入口、未分类、双 OCR 确认 script_display_name。
  供任意 LLM/Agent 运行时读取；触发条件见正文「何时须阅读本文件」。
---

# 何时须阅读本文件

凡运行本仓库「按键精灵自动化主脑」的模型，在出现以下**任一**情况时，必须先读取本文件全文（相对仓库根：`skills/keywizard-run-script/SKILL.md`），再决定下一步工具调用：

- 即将调用或已调用 `open_keywizard_app`，或当前前台为按键精灵相关界面；
- 需要在画面中定位「写脚本」类入口、「未分类」或脚本列表；
- 需要根据配置加载并启动指定脚本（`keywizard.script_display_name`）；
- 用户提到按键精灵跑脚本、写脚本、脚本列表、双 OCR 确认等与本流程相关的要求。

若运行环境支持读文件：用读文件工具载入上述路径。若不支持：由部署方在系统提示中注入本文件全文。

与本仓库主脑工具配合：成功跑通后会在 `experiences/agent_skills/` 自动生成由模型总结的短 Markdown；主脑可用 `list_learned_skills` / `read_learned_skill` 按需读取多条以加速，但以当前 OCR 为准，不可盲信历史。

---

# 按键精灵脚本运行（主脑操作规范）

本仓库主脑通过 ADB 工具操作设备；画面文字以 **OCR** 为准（每轮用户消息含 OCR，工具内可用 `wait_seconds`、`get_ocr_summary`、`tap_coordinate`、`tap_and_observe`、`open_keywizard_app`、`list_learned_skills`、`read_learned_skill` 等）。目标脚本显示名来自 **`config/settings.yaml` 中 `keywizard.script_display_name`**（勿写成拼写错误的键名）。

## 启动后等待

调用 `open_keywizard_app` 启动按键精灵后，**先 `wait_seconds` 约 2 秒**，再开始用 OCR 看画面。

## 阶段一：找到「写脚本」

1. 取得当前画面 OCR：依赖当轮上下文中的 OCR 块，或调用 **`get_ocr_summary`**（会截屏并 OCR）。
2. 在 OCR 结果中查找是否出现 **「写脚本」**（允许与 UI 一致的变体，如「写脚本（需root）」等，以 OCR 实际识别到的子串为准）。
3. **若没有**：再 `wait_seconds` 短等待（如 0.5～1s）后再次 **`get_ocr_summary`**，重复直到出现或达到合理重试上限后改策略（返回、再开 App、处理广告等）。
4. **若有**：用 OCR 给出的该行 **中心坐标** 调用 **`tap_and_observe`** 或 **`tap_coordinate`** 点入（优先 `tap_and_observe` 观察变化）。

## 阶段二：进入「未分类」

进入写脚本相关界面后，**再执行一次 OCR**（`get_ocr_summary` 或下一轮自带 OCR），在结果中定位 **「未分类」**，用其坐标 **点击进入**。

## 阶段三：脚本列表中选目标（双 OCR 一致才点）

1. **读取配置**：打开仓库内 **`config/settings.yaml`**，读取 **`keywizard.script_display_name`** 的字符串值，作为唯一目标名（与配置完全一致为最佳；若 OCR 有缺字，在多条候选中取**与目标名最相似**的一条）。
2. 当前列表界面 **第一次 OCR**：在 OCR 的多条脚本名中，找出与 `script_display_name` **最相似**的一项，记下其 **坐标与识别到的文字**。
3. **第二次 OCR**（重新 `get_ocr_summary` 或等新截图 OCR）：再次做**同一套**相似度匹配，得到第二项的 **坐标与文字**。
4. **通过条件**：两次匹配必须指向 **同一条脚本**（同一显示名或同一稳定坐标邻域；若两次指向不同行，**不得点击**，应滑动列表或等待后重新双 OCR，直到两次一致）。
5. **通过后**：对确认的坐标 **点击** 进入该脚本，之后 **按脚本内提示/界面指引** 继续（加载、启动等），直至可调用 `report_flow_done` 汇报结果。

## 工具与注意点

- **不要**用 monkey 启动按键精灵；打开 App 只用 **`open_keywizard_app`**（`am start -n`）。
- OCR 行含义：`- (x, y) '文字' (置信度)`，点击使用其中的 **(x, y)**。
- 广告或过渡动画：可先 **`wait_seconds`** 再 OCR，避免误判。
- 按键精灵阶段固定 **OCR + 主脑**（无多模态职员），更需严格遵守 **双 OCR 一致** 再点脚本名。
