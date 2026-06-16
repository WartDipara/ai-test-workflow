"""按设备/任务隔离的 OCR 专用工作线程，串行化 Paddle 推理避免无锁争用。"""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future
from typing import Any, Callable, TypeVar

from game_agent.models.settings import OcrSection

logger = logging.getLogger(__name__)

T = TypeVar("T")

V6_TINY_DET = "PP-OCRv6_tiny_det"
V6_TINY_REC = "PP-OCRv6_tiny_rec"

_workers_lock = threading.Lock()
_workers: dict[str, OcrWorker] = {}
_active_key = threading.local()


def set_active_ocr_worker_key(key: str) -> None:
    _active_key.key = key


def get_active_ocr_worker_key() -> str:
    return getattr(_active_key, "key", None) or "default"


def resolve_ocr_worker_key(explicit: str | None = None) -> str:
    return explicit or get_active_ocr_worker_key()


class OcrWorker:
    """单后台线程串行执行 OCR；每 worker_key 一个实例（批跑每设备一线程）。"""

    def __init__(self, cfg: OcrSection, *, worker_id: str) -> None:
        self.worker_id = worker_id
        self._cfg = cfg
        self._queue: queue.Queue[tuple[Callable[..., T], tuple, dict, Future[T]] | None] = (
            queue.Queue()
        )
        self._thread = threading.Thread(
            target=self._loop,
            name=f"ocr-worker-{worker_id}",
            daemon=True,
        )
        self._ocr_instance: Any = None
        self._paddle_v3 = False
        self._effective_device = "cpu"
        self._device_fallback_reason: str | None = None
        self._started = False
        self._shutdown = False

    @property
    def cfg(self) -> OcrSection:
        return self._cfg

    @property
    def effective_device(self) -> str:
        return self._effective_device

    @property
    def device_fallback_reason(self) -> str | None:
        return self._device_fallback_reason

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def _cfg_affects_model(self, cfg: OcrSection) -> bool:
        return (
            cfg.model_profile != self._cfg.model_profile
            or cfg.max_image_width != self._cfg.max_image_width
            or cfg.device_policy != self._cfg.device_policy
            or cfg.gpu_id != self._cfg.gpu_id
            or cfg.allow_gpu_fallback_to_cpu != self._cfg.allow_gpu_fallback_to_cpu
        )

    def reconfigure(self, cfg: OcrSection) -> None:
        """配置变更时丢弃已加载模型（在 worker 线程内执行）。"""
        if not self._cfg_affects_model(cfg):
            self._cfg = cfg
            return
        self._cfg = cfg
        self.submit(self._reset_model)

    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if self._shutdown:
            raise RuntimeError(f"OcrWorker {self.worker_id} is shut down")
        if not self._started:
            self.start()
        if threading.current_thread() is self._thread:
            return fn(*args, **kwargs)
        future: Future[T] = Future()
        self._queue.put((fn, args, kwargs, future))
        return future.result()

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._queue.put(None)
        if self._started:
            self._thread.join(timeout=30.0)

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            fn, args, kwargs, future = item
            try:
                future.set_result(fn(*args, **kwargs))
            except Exception as exc:
                future.set_exception(exc)

    def _reset_model(self) -> None:
        self._ocr_instance = None
        self._effective_device = "cpu"
        self._device_fallback_reason = None

    def force_cpu_fallback(self, reason: str) -> None:
        """推理阶段 GPU 失败时降级 CPU（须在 worker 线程内调用）。"""
        if not self._effective_device.startswith("gpu"):
            return
        logger.warning(
            "OCR GPU inference failed worker=%s: %s; falling back to cpu",
            self.worker_id,
            reason[:300],
        )
        self._ocr_instance = None
        self._effective_device = "cpu"
        self._device_fallback_reason = reason[:500]

    def get_ocr_instance(self) -> Any:
        if self._ocr_instance is not None:
            return self._ocr_instance
        inst, paddle_v3, device, fb = _build_paddle_ocr_instance(
            self._cfg,
            worker_id=self.worker_id,
        )
        self._ocr_instance = inst
        self._paddle_v3 = paddle_v3
        self._effective_device = device
        self._device_fallback_reason = fb
        return self._ocr_instance

    @property
    def paddle_v3(self) -> bool:
        if self._ocr_instance is None:
            return _paddle_major_version() >= 3
        return self._paddle_v3


def _configure_paddle_runtime() -> None:
    import os

    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")


def _cuda_available() -> bool:
    try:
        import paddle

        if not paddle.device.is_compiled_with_cuda():
            return False
        return int(paddle.device.cuda.device_count()) > 0
    except Exception:
        return False


def resolve_ocr_runtime_device(cfg: OcrSection) -> tuple[str, str | None]:
    """
    根据 device_policy 选择 PaddleOCR device 字符串。
    返回 (device, fallback_reason)；fallback_reason 仅在主动降级时非空。
    """
    policy = cfg.device_policy
    gpu_id = cfg.gpu_id
    gpu_dev = f"gpu:{gpu_id}"

    if policy == "cpu":
        return "cpu", None

    if policy == "gpu":
        if _cuda_available():
            return gpu_dev, None
        if cfg.allow_gpu_fallback_to_cpu:
            return "cpu", "device_policy=gpu but CUDA unavailable"
        raise RuntimeError("device_policy=gpu but CUDA is not available")

    # auto
    if _cuda_available():
        return gpu_dev, None
    return "cpu", None


def _paddleocr_version() -> str:
    try:
        import paddleocr

        return str(getattr(paddleocr, "__version__", "unknown"))
    except Exception:
        return "unknown"


def _paddle_version() -> str:
    try:
        import paddle

        return str(getattr(paddle, "__version__", "unknown"))
    except Exception:
        return "unknown"


def _create_paddle_ocr(cfg: OcrSection, device: str) -> tuple[Any, bool]:
    _configure_paddle_runtime()
    from paddleocr import PaddleOCR

    major = _paddle_major_version()
    paddle_v3 = major >= 3
    if not paddle_v3:
        return PaddleOCR(use_angle_cls=False, lang="ch", show_log=False), paddle_v3

    kwargs: dict = {
        "lang": "ch",
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "enable_mkldnn": False,
        "text_detection_model_name": V6_TINY_DET,
        "text_recognition_model_name": V6_TINY_REC,
        "device": device,
    }
    return PaddleOCR(**kwargs), paddle_v3


def _build_paddle_ocr_instance(
    cfg: OcrSection,
    *,
    worker_id: str = "",
) -> tuple[Any, bool, str, str | None]:
    device, probe_fb = resolve_ocr_runtime_device(cfg)
    try:
        inst, paddle_v3 = _create_paddle_ocr(cfg, device)
        fb = probe_fb
        logger.info(
            "OCR init worker=%s policy=%s effective_device=%s profile=%s "
            "paddleocr=%s paddle=%s%s",
            worker_id or "-",
            cfg.device_policy,
            device,
            cfg.model_profile,
            _paddleocr_version(),
            _paddle_version(),
            f" fallback={fb}" if fb else "",
        )
        return inst, paddle_v3, device, fb
    except Exception as exc:
        if device.startswith("gpu") and cfg.allow_gpu_fallback_to_cpu:
            reason = f"gpu init failed: {exc}"
            logger.warning(
                "OCR GPU init failed worker=%s: %s; falling back to cpu",
                worker_id or "-",
                exc,
            )
            inst, paddle_v3 = _create_paddle_ocr(cfg, "cpu")
            logger.info(
                "OCR init worker=%s policy=%s effective_device=cpu profile=%s "
                "paddleocr=%s paddle=%s fallback=%s",
                worker_id or "-",
                cfg.device_policy,
                cfg.model_profile,
                _paddleocr_version(),
                _paddle_version(),
                reason[:200],
            )
            return inst, paddle_v3, "cpu", reason
        raise


def _paddle_major_version() -> int:
    try:
        import paddleocr

        ver = getattr(paddleocr, "__version__", "0")
        return int(str(ver).split(".", maxsplit=1)[0])
    except Exception:
        return 2


def get_ocr_worker(*, worker_key: str | None = None) -> OcrWorker:
    key = resolve_ocr_worker_key(worker_key)
    with _workers_lock:
        worker = _workers.get(key)
        if worker is None:
            worker = OcrWorker(OcrSection(), worker_id=key)
            worker.start()
            _workers[key] = worker
        return worker


def configure_ocr_worker(cfg: OcrSection, *, worker_key: str | None = None) -> OcrWorker:
    key = resolve_ocr_worker_key(worker_key)
    set_active_ocr_worker_key(key)
    with _workers_lock:
        worker = _workers.get(key)
        if worker is None:
            worker = OcrWorker(cfg, worker_id=key)
            worker.start()
            _workers[key] = worker
        else:
            worker.reconfigure(cfg)
        _workers[key] = worker
        return worker


def shutdown_ocr_worker(worker_key: str | None = None) -> None:
    key = resolve_ocr_worker_key(worker_key)
    with _workers_lock:
        worker = _workers.pop(key, None)
    if worker is not None:
        worker.shutdown()
