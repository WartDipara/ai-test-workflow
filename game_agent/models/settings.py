from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class LLMSection(BaseModel):
    """任意 OpenAI 兼容 LLM 端点（主脑 / 多模态均可复用此结构）。"""

    base_url: str = Field(..., description="OpenAI 兼容 API base URL")
    api_key: str = Field(..., description="API Key，可配合 YAML 中的 ${ENV} 由加载器展开")
    model_name: str = Field(..., description="厂商 model 字段，如 deepseek-v4-flash、gpt-4o 等")


class DeepSeekSection(BaseModel):
    """
    仅当 llm 或 llm_multimodal 的 model_name 为 DeepSeek 官方模型时生效。
    与 llm 段解耦，避免主脑配置与单一厂商绑定。
    """

    thinking: bool = Field(
        True,
        description="官方思考模式；false 时不注入 thinking / reasoning_effort。",
    )
    reasoning_effort: Literal["high", "max"] = Field(
        "high",
        description=(
            "思考强度（仅 thinking=true）：官方支持 high、max。"
            "见 https://api-docs.deepseek.com/zh-cn/guides/thinking_mode"
        ),
    )
    tool_calls_strict: bool = Field(
        False,
        description=(
            "Beta strict Tool Calls：为 true 时使用 https://api.deepseek.com/beta，"
            "且工具定义须符合官方 strict JSON Schema。"
            "见 https://api-docs.deepseek.com/zh-cn/guides/tool_calls"
        ),
    )


class ObserverSection(BaseModel):
    """观察者阶段（多模态截图判定 / 画面监控），与主脑 llm 无关。"""

    skip_vision_probe: bool = Field(
        False,
        description="为 true 时跳过启动前对 llm_multimodal 的多模态探针（调试用）。",
    )


def is_deepseek_model(model_name: str) -> bool:
    name = (model_name or "").lower().strip()
    return name.startswith("deepseek-") or name in ("deepseek-chat", "deepseek-reasoner")


class AdbSection(BaseModel):
    serial: str | None = Field(None, description="adb -s；为空则省略")


class OcrSection(BaseModel):
    """PaddleOCR 性能与模型配置（PP-OCRv6 tiny + GPU/CPU 自动分流）。"""

    model_profile: Literal["v6_tiny"] = Field(
        "v6_tiny",
        description="固定使用 PP-OCRv6_tiny_det/rec（需 paddleocr>=3.7）。",
    )
    max_image_width: int = Field(
        720,
        ge=480,
        le=1920,
        description="识别前将截图等比缩放到不超过该宽度，坐标会映射回原分辨率。",
    )
    device_policy: Literal["auto", "gpu", "cpu"] = Field(
        "auto",
        description="auto：有 CUDA GPU 则用 GPU，否则 CPU；gpu/cpu 强制指定。",
    )
    gpu_id: int = Field(
        0,
        ge=0,
        le=7,
        description="device_policy 为 auto/gpu 时使用的 GPU 编号。",
    )
    allow_gpu_fallback_to_cpu: bool = Field(
        True,
        description="GPU 初始化或推理失败时是否自动降级 CPU。",
    )
    warmup_on_start: bool = Field(
        False,
        description="进程启动后是否预热 OCR（首次推理仍较慢，默认关以免拖慢开局 am start）。",
    )


class MolmopointSection(BaseModel):
    """MolmoPoint 深度学习 checkbox 定位服务（FastAPI /predict）。"""

    base_url: str = Field(
        "",
        description="MolmoPoint API 根地址，如 http://192.168.1.10:8000；留空则仅用 OCR 左推。",
    )
    enabled: bool = Field(
        True,
        description="为 false 时不请求 MolmoPoint，即使配置了 base_url。",
    )
    timeout_s: float = Field(
        60.0,
        ge=5.0,
        le=300.0,
        description="predict 请求超时（秒）。",
    )
    prompt: str = Field(
        "point the checkbox",
        description="传给 MolmoPoint 的文本提示。",
    )
    max_vertical_offset_ratio: float = Field(
        0.55,
        ge=0.1,
        le=1.5,
        description="预测点纵坐标与 OCR 协议行中心允许的最大偏差（相对行高）。",
    )
    min_left_of_text_px: int = Field(
        4,
        ge=0,
        le=80,
        description="预测点须位于 OCR 锚点左缘以左至少该像素。",
    )
    max_left_of_text_px: int = Field(
        400,
        ge=40,
        le=800,
        description="预测点距 OCR 锚点左缘向左不超过该像素（过远视为无效）。",
    )

    def is_active(self) -> bool:
        return self.enabled and bool((self.base_url or "").strip())


class ExecutorSection(BaseModel):
    """LangGraph 进游戏流程：开局等待与登录填表参数。"""

    post_launch_wait_s: float = Field(
        2.0,
        ge=0.5,
        le=10.0,
        description="开局或 open_game_app 后等待界面稳定的秒数。",
    )
    credential_fill_settle_s: float = Field(
        0.4,
        ge=0.15,
        le=2.0,
        description="无障碍填表：点击输入框后等待焦点稳定的秒数。",
    )
    dismiss_keyboard_after_password: bool = Field(
        True,
        description="填完密码后自动点击屏幕右上角空白区收起安全键盘（坐标按 wm size 比例计算）。",
    )
    dismiss_keyboard_press_back: bool = Field(
        False,
        description="收起键盘时在点击空白区后再按 BACK（部分游戏可能返回上一页，默认关）。",
    )
    credential_verify_after_fill: bool = Field(
        True,
        description="填表后无障碍回读节点：校验对准 OCR 坐标且文本/掩码长度正确；失败自动重填一次。",
    )
    credential_fill_max_distance_px: float = Field(
        150.0,
        ge=40.0,
        le=400.0,
        description="填表校验：EditText 中心与 OCR 点击点允许的最大像素距离。",
    )
    credential_fill_retry_on_verify_fail: bool = Field(
        True,
        description="校验失败时自动再 setText 一次。",
    )
    login_submit_press_enter: bool = Field(
        True,
        description="密码填完后对焦点发送 ENTER（部分游戏可直接提交）。",
    )
    use_cached_login_button_xy: bool = Field(
        True,
        description="atomic_login 使用 OCR 阶段缓存的登录按钮坐标（安全键盘黑屏时仍可点击）。",
    )
    server_panel_fusion_enabled: bool = Field(
        True,
        description="区服 tap 后 OCR+多模态双路并行融合判定弹窗是否打开。",
    )
    server_panel_vision_min_conf: float = Field(
        0.75,
        ge=0.5,
        le=1.0,
        description="Vision 单独救场（OCR 未命中）时的最低置信度。",
    )


class GameSection(BaseModel):
    """游戏流程超时与判定参数；包名/Activity 由 TaskRuntime 管理。"""
    timeout_s: float = Field(300.0, description="并行监控的最大允许时间（秒），超时算作异常。")
    package_install_wait_timeout_s: float = Field(
        120.0,
        ge=10.0,
        le=600.0,
        description="deploy 后等待设备上出现目标包名的最长时间（秒）。",
    )
    package_install_poll_interval_s: float = Field(
        2.0,
        ge=0.5,
        le=15.0,
        description="检测包是否已安装的轮询间隔（秒）。",
    )
    main_screen_confirm_rounds: int = Field(
        2,
        ge=1,
        le=5,
        description="连续多少轮 AI 均判 in_game_main 才确认进入游戏。",
    )
    main_screen_min_confidence: float = Field(
        0.75,
        ge=0.5,
        le=1.0,
        description="进入游戏判定的最低 confidence。",
    )
    normal_exit_observe_s: float = Field(
        10.0,
        ge=1.0,
        le=120.0,
        description="确认进入游戏后、force-stop 前的加速观察窗口（秒）。",
    )
    stability_observe_s: float = Field(
        60.0,
        ge=10.0,
        le=180.0,
        description="进游戏确认后、终局 signal 前的稳定性观察总时长（秒）。",
    )
    stability_check_interval_s: float = Field(
        15.0,
        ge=5.0,
        le=60.0,
        description="稳定性观察期间两次多模态轮询的最小间隔（秒）。",
    )
    session_poll_interval_s: float = Field(
        1.5,
        ge=0.5,
        le=10.0,
        description="观察者阶段检测游戏进程是否 crash/重启的轮询间隔（秒）。",
    )
    session_absent_threshold_s: float = Field(
        2.0,
        ge=0.5,
        le=30.0,
        description="进程连续缺失多久视为一次 crash（秒）。",
    )
    clear_logcat_on_session_restart: bool = Field(
        True,
        description="游戏会话重启时是否 adb logcat -c 并重新采集 GameTurbo 日志。",
    )
    max_session_restarts: int = Field(
        0,
        ge=0,
        le=50,
        description="单轮观察者允许的最大会话重启次数；0 表示不限制。",
    )

class GameTurboSection(BaseModel):
    """GameTurbo deploy 静态参数；gid/路径由 TaskRuntime 管理。"""

    deploy_timeout_s: float = Field(
        900.0,
        ge=60.0,
        le=3600.0,
        description="GameTurbo deploy.sh 的最长等待时间。",
    )
    deploy_max_ai_retries: int = Field(
        3,
        ge=1,
        le=10,
        description="单次 deploy 流程内失败时，AI 分析 deploy.log 后重试 deploy 的最大次数（含首次）。",
    )
    modify_patch_max_llm_retries: int = Field(
        3,
        ge=1,
        le=10,
        description="Modify 阶段生成配置补丁时，LLM 请求失败后的最大重试次数（含首次）。",
    )
    run_outputs_dir: Path = Field(
        Path("./run_outputs"),
        description="单次任务最终产出目录根路径，子目录为 {gid}_{task_id}。",
    )

class CredentialsSection(BaseModel):
    """游戏登录账号密码（独立 YAML，勿提交 git）。"""

    file_path: Path = Field(
        Path("./credentials.yaml"),
        description="凭据文件路径，含 username 与 password 字段。",
    )


class PreprocessingSection(BaseModel):
    """预处理阶段配置：APK 下载/ABI 剥离。在 retry 循环前执行一次。"""

    enabled: bool = Field(
        True,
        description="为 true 时在 retry 循环之前执行预处理阶段。",
    )
    apk_cache_dir: Path = Field(
        Path("./apk_cache"),
        description=(
            "APK 本地缓存目录；从 apks.txt 读取链接并下载到此目录，"
            "ABI 剥离后移动到 packages。"
        ),
    )
    preserved_abis: list[str] = Field(
        default_factory=lambda: ["arm64-v8a", "armeabi-v7a"],
        description="GameTurbo 支持的 ARM ABI 列表；lib/ 下其他 ABI 目录将被移除。",
    )


class NetworkAnomalySection(BaseModel):
    """并行阶段 OCR+多模态网络异常监视（运行期不分析日志）。"""

    enabled: bool = Field(
        True,
        description="为 true 时在并行阶段运行 NetworkAnomalyCoordinator（OCR+多模态）。",
    )
    poll_interval_s: float = Field(
        5.0,
        ge=2.0,
        le=30.0,
        description="OCR/多模态轮询间隔（秒）。",
    )
    require_multimodal_confirm: bool = Field(
        True,
        description="为 true 时 OCR suspect 后须多模态 has_anomaly 确认才 fatal（无多模态时高置信 OCR 弹窗可例外）。",
    )
    download_progress_stall_s: float = Field(
        90.0,
        ge=30.0,
        le=600.0,
        description="资源下载进度/阶段不变多久视为 OCR suspect。",
    )
    use_ocr_poll: bool = Field(
        True,
        description="是否独立 OCR 轮询（不依赖执行者 analyze_screen）。",
    )
    exclude_top_ratio: float = Field(
        0.15,
        ge=0.05,
        le=0.35,
        description="OCR 忽略画面上方比例（GameTurbo 加速角标区域）。",
    )


class ModulesSection(BaseModel):
    """流水线模块开关；便于单独测试各子系统。默认均为 true。"""

    executor: bool = Field(
        True,
        description="Driver：AI + OCR + adb tap 直至 check_in_game 确认进游戏（与 monitors 并行）。",
    )
    log_monitor: bool = Field(
        True,
        description="Monitor：并行采集 GameTurbo logcat，供双通道网络异常监视使用。",
    )
    retry_on_failure: bool = Field(
        True,
        description=(
            "为 true 时：失败收尾后执行 AI 分析/deploy 并进入下一轮。"
            "为 false 时：仍执行失败收尾（导出日志/杀进程/卸载），但不 deploy、不重试。"
        ),
    )
    max_retries: int = Field(
        3,
        ge=1,
        le=10,
        description="retry_on_failure 为 true 时的最大尝试次数；为 false 时仅跑 1 次。",
    )


class AgentSection(BaseModel):
    artifacts_dir: Path = Field(Path("./artifacts"))


class LoggingSection(BaseModel):
    level: str = Field("INFO")
    enable_run_audit: bool = Field(
        True,
        description="为 true 时在 artifacts 目录写入 audit/（思考、工具、阶段事件）。",
    )
    enable_process_log_file: bool = Field(
        True,
        description="为 true 时将标准 logging 同时写入 artifact_root/process.log。",
    )
    enable_pipeline_trace: bool = Field(
        True,
        description="为 true 时记录流水线调用追踪（component/operation/status/结果），写入 process.log 与 pipeline_trace.jsonl。",
    )
    pipeline_trace_verbose: bool = Field(
        True,
        description="为 true 时追踪日志包含完整 detail；为 false 时仅保留关键字段（成熟后可关闭以减量）。",
    )


class AppConfig(BaseModel):
    """根配置，对应一份 YAML。"""

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_nested_fields(cls, data: Any) -> Any:
        """迁移 YAML：剥离运行态字段，修正 llm 段历史嵌套。"""
        if not isinstance(data, dict):
            return data
        data.pop("detection", None)
        agent = data.get("agent")
        if isinstance(agent, dict):
            for key in (
                "max_rounds",
                "persist_learned_skill_on_success",
                "tap_observe_count",
                "repeat_compact_stage_hint_every_n_rounds",
            ):
                agent.pop(key, None)
        executor = data.get("executor")
        if isinstance(executor, dict):
            for key in ("ad_initial_wait_s", "max_foreground_retries"):
                executor.pop(key, None)
        game = data.get("game")
        if isinstance(game, dict):
            game.pop("package_name", None)
            game.pop("launch_activity", None)
            for key in ("launch_detect_timeout_s", "launch_detect_poll_interval_s"):
                game.pop(key, None)
        gameturbo = data.get("gameturbo")
        if isinstance(gameturbo, dict):
            gameturbo.pop("gid", None)
            gameturbo.pop("game_config_path", None)
            gameturbo.pop("source_apk", None)
        llm = data.get("llm")
        if isinstance(llm, dict):
            for key in ("deepseek_litellm_compat", "deepseek_thinking_mode"):
                llm.pop(key, None)
            if "deepseek_thinking" in llm:
                deepseek = data.setdefault("deepseek", {})
                if isinstance(deepseek, dict):
                    deepseek.setdefault("thinking", llm.pop("deepseek_thinking"))
            if "skip_vision_probe" in llm:
                observer = data.setdefault("observer", {})
                if isinstance(observer, dict):
                    observer.setdefault("skip_vision_probe", llm.pop("skip_vision_probe"))
        return data

    @model_validator(mode="after")
    def _deepseek_llm_requires_official_base_url(self) -> AppConfig:
        for label, section in (("llm", self.llm), ("llm_multimodal", self.llm_multimodal)):
            if section is None or not is_deepseek_model(section.model_name):
                continue
            base = section.base_url.rstrip("/").lower()
            if not base.startswith("https://api.deepseek.com"):
                raise ValueError(
                    f"{label} 使用 DeepSeek 模型时须为官方端点 base_url=https://api.deepseek.com "
                    f"（当前为 {section.base_url!r}）。文档: https://api-docs.deepseek.com/zh-cn/"
                )
        return self

    @model_validator(mode="after")
    def _require_multimodal_when_executor(self) -> AppConfig:
        if self.modules.executor and self.llm_multimodal is None:
            raise ValueError(
                "modules.executor 为 true 时 llm_multimodal 必填（check_in_game 依赖视觉模型）"
            )
        return self

    llm: LLMSection
    llm_multimodal: LLMSection | None = Field(
        None,
        description=(
            "专门处理多模态（视觉）任务的 LLM 配置。"
            "如果为空，则默认使用主 LLM（如果主 LLM 支持）。"
        ),
    )
    deepseek: DeepSeekSection = Field(
        default_factory=DeepSeekSection,
        description="仅当某 LLM 段的 model 为 DeepSeek 官方模型时使用。",
    )
    observer: ObserverSection = Field(default_factory=ObserverSection)
    adb: AdbSection = Field(default_factory=AdbSection)
    ocr: OcrSection = Field(default_factory=OcrSection)
    molmopoint: MolmopointSection = Field(default_factory=MolmopointSection)
    executor: ExecutorSection = Field(default_factory=ExecutorSection)
    game: GameSection
    gameturbo: GameTurboSection = Field(default_factory=GameTurboSection)
    credentials: CredentialsSection = Field(default_factory=CredentialsSection)
    preprocessing: PreprocessingSection = Field(default_factory=PreprocessingSection)
    modules: ModulesSection = Field(default_factory=ModulesSection)
    network_anomaly: NetworkAnomalySection = Field(default_factory=NetworkAnomalySection)
    agent: AgentSection = Field(default_factory=AgentSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
