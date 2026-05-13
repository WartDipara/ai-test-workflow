# android-ai-driven-test

面向 **Android 游戏** 的自动化编排实验仓库：核心为 **`game_agent`**（Pydantic-AI + ADB），用于在设备/模拟器上**自主决策完成登录等 UI 流程**。  
**GameTurbo-Native** 为独立子项目，本仓库阶段一**仅通过你本地手动执行**其 `GameTurbo-Native/client/android/deploy.sh` 完成打包安装即可，与 `game_agent` 解耦。

---

## 环境前提

| 项 | 说明 |
|----|------|
| Conda | 已安装 Miniconda / Anaconda |
| Python | **≥ 3.12**（与 `pyproject.toml` 中 `requires-python` 一致） |
| ADB | 已安装 [Android Platform-Tools](https://developer.android.com/tools/releases/platform-tools)，`adb` 在 **PATH** 中可用 |
| LLM | 任意 **OpenAI Chat Completions 兼容** API（在配置中填写 `base_url`、`api_key`、`model_name`）；见下文「LLM 适配器与 DeepSeek」 |

---

## 从零初始化（仅 Conda + 本仓库）

### 1. 创建并激活 Conda 环境

若尚无合适环境（示例环境名 `nx`，可改成你的习惯名）：

```bash
conda create -n nx python=3.12 -y
conda activate nx
```

若已有环境但 Python 低于 3.12，请新建或升级到 3.12+。

### 2. 进入仓库根目录

```bash
cd /path/to/android-ai-driven-test
```

Windows 示例：

```powershell
cd D:\smwl\android-ai-driven-test
```

### 3. 安装 Python 依赖

在**已激活**的 Conda 环境中执行：

```bash
python -m pip install -U pip setuptools wheel
python -m pip install -e .
```

可选（开发用静态检查）：

```bash
python -m pip install -e ".[dev]"
```

依赖说明见根目录 [pyproject.toml](pyproject.toml)（核心：`pydantic-ai-slim[openai]`、`pydantic`、`pydantic-settings`、`pyyaml`；另有 **`httpx`**、**`Pillow`** 用于截图拉取/缩放与 `text_base64` 图像通路）。  
若使用 **Python 3.14** 等较新版本，请保持使用 **`pydantic-ai-slim[openai]`**（与当前 `pyproject.toml` 一致），避免安装完整 `pydantic-ai` 时因可选依赖解析失败。

### 4. 验证安装

```bash
python -m game_agent.main --help
```

能打印 `usage: ...` 即表示 **`game_agent` 已正确装入当前环境**。

`-m` 含义：以「模块」方式运行脚本，等价于执行包内 `game_agent/main.py` 的入口，有利于包路径与导入解析。

### 5. 配置文件

1. 复制示例并编辑主配置：

   ```bash
   copy config\settings.example.yaml config\settings.yaml
   ```

   Linux / macOS：

   ```bash
   cp config/settings.example.yaml config/settings.yaml
   ```

2. 编辑 `config/settings.yaml`：
   - `llm.base_url`：API 根地址（如 `https://api.openai.com/v1` 或 DeepSeek 等兼容网关）。
   - `llm.model_name`：模型名（如 `gpt-4o`；若名称同时包含 `deepseek` 与 `v4-flash` 或 `v4-pro`，会走 **DeepSeek 适配器**，见「LLM 适配器与 DeepSeek」）。
   - `llm.api_key`：密钥；**推荐**写成 `${OPENAI_API_KEY}`，再在系统中设置同名环境变量，避免明文落盘。
   - `game.package_name` / `game.activity`：目标游戏包名与可选 Activity。
   - `llm.image_transport`：`openai_multimodal`（默认）或 `text_base64`（截图以 Base64 写入纯文本，见 README 常见问题）。
   - `agent.screenshot_max_edge`：在 `text_base64` 模式下缩放截图最长边，减轻 token。
   - `credentials.file_path`：账号密码 YAML 路径（相对路径**相对于 `settings.yaml` 所在目录**解析）。

3. 准备凭据文件（路径需与 `credentials.file_path` 一致），默认示例为与 `settings` 同目录的 `credentials.yaml`：

   ```bash
   copy config\credentials.example.yaml config\credentials.yaml
   ```

   填写 `username`、`password`。`config/credentials.yaml` 与 `config/settings.yaml` 已在 [.gitignore](.gitignore) 中忽略，勿提交仓库。

   **路径说明**：`credentials.file_path` 若为相对路径，则**相对于固定主配置 `config/settings.yaml` 所在目录**（即仓库内 `config/`）解析。  
   例如 `file_path: "./credentials.yaml"` 会去找 `config/credentials.yaml`；若该路径下没有文件就会报「凭据文件不存在」。

### 6. 设备与 GameTurbo（阶段一）

1. 启动模拟器或连接真机，确认：

   ```bash
   adb devices
   ```

   状态应为 `device`。

2. **游戏安装**：当前阶段请你在本机用 **Git Bash** 等方式手动执行：

   `GameTurbo-Native/client/android/deploy.sh`

   完成注入与安装后，再运行下面的 `game_agent`。

### 7. 运行登录 Agent

在仓库根目录、已激活 Conda 环境、配置就绪后：

```bash
python -m game_agent.main
```

主配置**固定**为仓库根目录下 [config/settings.yaml](config/settings.yaml)（由 `game_agent/main.py` 相对包路径解析，不依赖当前工作目录）；无需再传 `--config`。

- 成功结束（模型调用 `report_flow_done(success=true, ...)`）时进程退出码为 **0**。  
- 失败或达到最大轮次未结束为 **1**。  
- 每轮截图与运行产物默认在 `agent.artifacts_dir` 下带时间戳的子目录中（见配置）。

---

## LLM 适配器与 DeepSeek

为按厂商/系列定制请求格式（类似「按模型选 API 形态」），LLM 构造采用 **适配器 + 工厂**：

| 模块 | 作用 |
|------|------|
| [game_agent/services/llm_service.py](game_agent/services/llm_service.py) | `build_llm_model(llm)`：根据 `model_name` 选择适配器并返回 Pydantic-AI `Model` |
| [game_agent/services/llm_adapters/base.py](game_agent/services/llm_adapters/base.py) | `BaseModelAdapter`：抽象基类，子类实现 `build_model()` |
| [game_agent/services/llm_adapters/openai.py](game_agent/services/llm_adapters/openai.py) | `GenericOpenAIAdapter`：通用 OpenAI 兼容 Chat，无额外请求体 |
| [game_agent/services/llm_adapters/deepseek.py](game_agent/services/llm_adapters/deepseek.py) | `DeepSeekAdapter` + `DeepSeekThinkingModel`：`deepseek-v4-flash` / `deepseek-v4-pro` 等思考模式与回传逻辑 |

**路由规则（当前实现）**：`model_name`（小写）中同时包含 `deepseek` 以及 **`v4-flash` 或 `v4-pro`** 时，使用 `DeepSeekAdapter`；否则使用 `GenericOpenAIAdapter`。新增厂商时可在 `llm_service.py` 增加分支或改为表驱动。

**DeepSeek 思考模式**（与官方 [思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode) 对齐）：

- 请求侧：`DeepSeekThinkingModel.prepare_request` 注入 `reasoning_effort`（映射为 `high`，若你已在别处传入 `openai_reasoning_effort` 则不再覆盖）与 `extra_body["thinking"] = {"type": "enabled"}`。
- 文档要求：带 **工具调用** 的轮次之后，后续请求须把该轮 `assistant` 的 **`reasoning_content`** 一并回传，否则可能 **400**。实现上在 `DeepSeekThinkingModel._map_model_response` 中从 `ThinkingPart` 显式写入 `reasoning_content`，与 Pydantic-AI 对 `reasoning_content` 的解析链路一致。
- 思考模式下 `temperature`、`top_p` 等采样参数在服务端可能不生效（见官方说明），属预期行为。

**图像与 DeepSeek**：若网关不接受多模态 `image_url`，仍可在 `settings.yaml` 中设 `llm.image_transport: "text_base64"`（与适配器正交）。

登录 Agent 通过 `build_llm_model(app_config.llm)` 取模型，业务代码不直接依赖具体子类。

---

## 目录结构（与 MVC 对应）

| 路径 | 角色 |
|------|------|
| [game_agent/models/](game_agent/models/) | 配置模型、运行状态等（Model） |
| [game_agent/views/](game_agent/views/) | 控制台日志呈现（View） |
| [game_agent/controllers/](game_agent/controllers/) | 加载配置、驱动多轮 Agent（Controller） |
| [game_agent/services/](game_agent/services/) | ADB、凭据、LLM 工厂、多模态探针等（Service） |
| [game_agent/services/llm_adapters/](game_agent/services/llm_adapters/) | 按模型拆分的 LLM 适配器（`base` / `openai` / `deepseek`） |
| [game_agent/agents/](game_agent/agents/) | Pydantic-AI Agent 与工具注册 |
| [game_agent/config/](game_agent/config/) | YAML 加载与环境变量 `${VAR}` 展开 |
| [config/](config/) | 示例与本地 `settings.yaml`（勿提交密钥） |

---

## 常见问题

**Q：DeepSeek 思考模式 + 工具调用报 400，提示与 `reasoning_content` 相关？**  
A：请确认 `model_name` 命中上文路由（含 `deepseek` 与 `v4-flash` 或 `v4-pro`），从而使用 [deepseek.py](game_agent/services/llm_adapters/deepseek.py) 中的 `DeepSeekThinkingModel`。仍失败时对照官方 [思考模式 · 工具调用](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode) 检查是否混用了不支持的采样参数或消息拼接顺序。

**Q：启动后立刻报「不支持多模态 / image_url」或 DeepSeek 400？**  
A：默认 `llm.image_transport: openai_multimodal` 会走 OpenAI Chat 的 `image_url` 部件。若网关**只接受纯 text**（报错里出现 `unknown variant image_url`），请在 `settings.yaml` 中设置：

```yaml
llm:
  image_transport: "text_base64"
```

此时每轮会把截图**缩放后转 Base64**，连同中文说明一起写在**用户文本**里（不经由 `image_url` 字段）；启动探针改为**纯文本连通性**检查。注意：token 占用更大，且依赖模型是否真的能「读」文本里的 Base64 图像语义。

仅调试用可在 `settings.yaml` 里设 `llm.skip_vision_probe: true` 跳过探针（**不推荐**：`openai_multimodal` 下首轮带图仍可能 400）。

**Q：`adb` 找不到？**  
A：安装 Platform-Tools 并将目录加入系统 **PATH**，重新打开终端后再试 `adb version`。

**Q：`llm.api_key` 报未展开？**  
A：若 YAML 中为 `${SOME_VAR}`，请确认当前 shell / 系统已导出 `SOME_VAR`，或改为直接写密钥（不推荐提交仓库）。

**Q：中文或特殊字符密码输入失败？**  
A：当前实现使用 `adb shell input text`，对部分字符有限制；后续可扩展剪贴板/无障碍等通道。

---

## 许可证与第三方

- 本仓库 `game_agent` 为自研结构；若你后续参考 [AppAgent](https://github.com/TencentQQGYLab/AppAgent) 等第三方代码复制片段，请遵守对方许可证并在文件头保留声明。  
- `GameTurbo-Native` 子树遵循其各自许可与说明。
