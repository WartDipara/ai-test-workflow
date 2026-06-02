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
    """PaddleOCR 性能与模型配置。"""

    model_profile: Literal["mobile", "server"] = Field(
        "mobile",
        description="mobile 使用 PP-OCRv5_mobile_*，显著快于 server；server 精度更高但更慢。",
    )
    max_image_width: int = Field(
        720,
        ge=480,
        le=1920,
        description="识别前将截图等比缩放到不超过该宽度，坐标会映射回原分辨率。",
    )
    warmup_on_start: bool = Field(
        False,
        description="进程启动后是否预热 OCR（首次推理仍较慢，默认关以免拖慢开局 am start）。",
    )


class ExecutorSection(BaseModel):
    """执行者阶段（OCR + AI tap）配置。"""

    ad_initial_wait_s: float = Field(
        3.0,
        ge=0.5,
        le=15.0,
        description="首次疑似广告页时优先等待的秒数。",
    )
    post_launch_wait_s: float = Field(
        2.0,
        ge=0.5,
        le=10.0,
        description="开局或 open_game_app 后等待界面稳定的秒数。",
    )
    max_foreground_retries: int = Field(
        4,
        ge=1,
        le=10,
        description="连续非游戏前台轮数的提示阈值，供主脑判断是否需要重新打开游戏。",
    )


class GameSection(BaseModel):
    package_name: str = Field(
        ...,
        description="测试游戏包名；force-stop / uninstall / 前台校验使用。",
    )
    launch_activity: str = Field(
        ...,
        description="am start -n 的完整组件串；由 APK 自动写入，禁止 monkey 启动游戏。",
    )
    timeout_s: float = Field(300.0, description="并行监控的最大允许时间（秒），超时算作异常。")
    launch_detect_timeout_s: float = Field(
        90.0,
        ge=15.0,
        le=600.0,
        description="执行者 wait_for_game_running 最长等待游戏进程秒数。",
    )
    launch_detect_poll_interval_s: float = Field(
        2.0,
        ge=0.5,
        le=15.0,
        description="等待游戏进程时的 pidof 轮询间隔（秒）。",
    )
    package_install_wait_timeout_s: float = Field(
        120.0,
        ge=10.0,
        le=600.0,
        description="deploy 后等待设备上出现 game.package_name 的最长时间（秒）。",
    )
    package_install_poll_interval_s: float = Field(
        2.0,
        ge=0.5,
        le=15.0,
        description="检测包是否已安装的轮询间隔（秒）。",
    )
    main_screen_detect_timeout_s: float = Field(
        240.0,
        ge=30.0,
        le=900.0,
        description="AI 判定「已进入游戏内」的最长等待秒数（进程已起之后）。",
    )
    main_screen_detect_poll_interval_s: float = Field(
        5.0,
        ge=2.0,
        le=30.0,
        description="进入游戏 AI 判定轮询间隔（秒）。",
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

    @model_validator(mode="after")
    def _normalize_launch_component(self) -> GameSection:
        package_name = self.package_name.strip()
        launch_activity = self.launch_activity.strip()
        if not package_name and not launch_activity:
            return self
        if package_name and not launch_activity:
            raise ValueError("game.launch_activity 不能为空（已配置 package_name）")
        if launch_activity and not package_name:
            raise ValueError("game.package_name 不能为空（已配置 launch_activity）")
        if "/" not in launch_activity:
            launch_activity = f"{package_name}/{launch_activity}"
        elif launch_activity.startswith("/"):
            launch_activity = f"{package_name}{launch_activity}"
        self.package_name = package_name
        self.launch_activity = launch_activity
        return self


class GameTurboSection(BaseModel):
    """GameTurbo 前置处理与重试阶段的运行上下文。"""

    gid: str = Field("", description="原包文件名前缀解析出的游戏 gid。")
    game_config_path: Path | None = Field(
        None,
        description="当前轮次允许修改的 games/gameturbo_<gid>_*.json。",
    )
    source_apk: Path | None = Field(
        None,
        description="初始化时发现的原始游戏 APK。",
    )
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
    run_outputs_dir: Path = Field(
        Path("./run_outputs"),
        description="单次任务最终产出目录根路径，子目录为 {gid}_{task_id}。",
    )

    @model_validator(mode="after")
    def _normalize_blank_paths(self) -> GameTurboSection:
        self.gid = self.gid.strip()
        if self.game_config_path is not None and not str(self.game_config_path).strip():
            self.game_config_path = None
        if self.source_apk is not None and not str(self.source_apk).strip():
            self.source_apk = None
        return self


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


class ModulesSection(BaseModel):
    """流水线模块开关；便于单独测试各子系统。默认均为 true。"""

    executor: bool = Field(
        True,
        description="Driver：AI + OCR + adb tap 直至 check_in_game 确认进游戏（与 monitors 并行）。",
    )
    log_monitor: bool = Field(
        True,
        description="Monitor：从游戏启动并行监听 GameTurbo logcat；高置信异常 fail-fast。",
    )
    screen_monitor: bool = Field(
        True,
        description="Monitor：并行截图；网络类弹窗 fail-fast（不替代执行者点 UI）。",
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
    max_rounds: int = Field(30, ge=1, le=200)
    artifacts_dir: Path = Field(Path("./artifacts"))
    persist_learned_skill_on_success: bool = Field(
        True,
        description="success=true 结束时调用主 LLM 将本轮对话压成短 Markdown 写入 experiences/agent_skills/。",
    )
    tap_observe_count: int = Field(
        2,
        ge=1,
        le=6,
        description="tap_and_observe 默认连拍 OCR 次数（执行者阶段；越少越快）。",
    )


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
        """将误写在 llm 下的厂商/观察者字段迁到独立段。"""
        if not isinstance(data, dict):
            return data
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
    def _require_game_when_executor(self) -> AppConfig:
        if self.modules.executor:
            if not self.game.package_name.strip():
                raise ValueError("game.package_name 不能为空（modules.executor 为 true 时）")
            if not self.game.launch_activity.strip():
                raise ValueError("game.launch_activity 不能为空（modules.executor 为 true 时）")
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
    executor: ExecutorSection = Field(default_factory=ExecutorSection)
    game: GameSection
    gameturbo: GameTurboSection = Field(default_factory=GameTurboSection)
    credentials: CredentialsSection = Field(default_factory=CredentialsSection)
    preprocessing: PreprocessingSection = Field(default_factory=PreprocessingSection)
    modules: ModulesSection = Field(default_factory=ModulesSection)
    agent: AgentSection = Field(default_factory=AgentSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
