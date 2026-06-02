from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from game_agent.models.settings import OcrSection

logger = logging.getLogger(__name__)


def _configure_paddle_runtime() -> None:
    """Windows + Paddle 3.x CPU 默认 oneDNN 可能触发 PIR 转换 NotImplementedError。"""
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")

_OCR_INSTANCE = None
_PADDLEOCR_V3 = False
_OCR_CONFIG: OcrSection = OcrSection()


def configure_ocr(cfg: OcrSection) -> None:
    """由 Controller 在 run 开始时注入；切换 profile 会重置已加载模型。"""
    global _OCR_CONFIG, _OCR_INSTANCE
    if cfg.model_profile != _OCR_CONFIG.model_profile or cfg.max_image_width != _OCR_CONFIG.max_image_width:
        _OCR_INSTANCE = None
    _OCR_CONFIG = cfg


def _paddle_major_version() -> int:
    try:
        import paddleocr

        ver = getattr(paddleocr, "__version__", "0")
        return int(str(ver).split(".", maxsplit=1)[0])
    except Exception:
        return 2


def get_ocr_instance():
    """懒加载 PaddleOCR；自动区分 2.x / 3.x 构造参数。"""
    global _OCR_INSTANCE, _PADDLEOCR_V3
    if _OCR_INSTANCE is not None:
        return _OCR_INSTANCE

    _configure_paddle_runtime()
    from paddleocr import PaddleOCR

    major = _paddle_major_version()
    _PADDLEOCR_V3 = major >= 3
    profile = _OCR_CONFIG.model_profile
    logger.info("Initializing PaddleOCR (major=%s, profile=%s)...", major, profile)

    if _PADDLEOCR_V3:
        kwargs: dict = {
            "lang": "ch",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "enable_mkldnn": False,
        }
        if profile == "mobile":
            kwargs["text_detection_model_name"] = "PP-OCRv5_mobile_det"
            kwargs["text_recognition_model_name"] = "PP-OCRv5_mobile_rec"
        else:
            kwargs["text_detection_model_name"] = "PP-OCRv5_server_det"
            kwargs["text_recognition_model_name"] = "PP-OCRv5_server_rec"
        _OCR_INSTANCE = PaddleOCR(**kwargs)
    else:
        _OCR_INSTANCE = PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)

    return _OCR_INSTANCE


@dataclass(frozen=True)
class _PreparedImage:
    path: Path
    scale_x: float
    scale_y: float
    temp_path: Path | None


def _prepare_image_for_ocr(image_path: Path) -> _PreparedImage:
    """必要时缩小截图以加速 OCR；返回用于推理的路径与坐标缩放比。"""
    from PIL import Image

    max_w = _OCR_CONFIG.max_image_width
    src = image_path.resolve()
    with Image.open(src) as im:
        im = im.convert("RGB")
        ow, oh = im.size
        if ow <= max_w:
            return _PreparedImage(path=src, scale_x=1.0, scale_y=1.0, temp_path=None)
        nw = max_w
        nh = max(1, int(oh * nw / ow))
        resized = im.resize((nw, nh), Image.Resampling.LANCZOS)
        tmp = src.parent / f"{src.stem}_ocr_{nw}w.png"
        resized.save(tmp, format="PNG")
        return _PreparedImage(
            path=tmp,
            scale_x=ow / nw,
            scale_y=oh / nh,
            temp_path=tmp,
        )


def _poly_center(poly, *, scale_x: float, scale_y: float) -> tuple[int, int]:
    xs: list[float] = []
    ys: list[float] = []
    for p in poly:
        xs.append(float(p[0]) * scale_x)
        ys.append(float(p[1]) * scale_y)
    if not xs:
        return 0, 0
    return int(sum(xs) / len(xs)), int(sum(ys) / len(ys))


def _format_v3_result(result, *, scale_x: float, scale_y: float) -> list[str]:
    lines: list[str] = []
    data = result if isinstance(result, dict) else dict(result)
    polys = data.get("rec_polys") or data.get("dt_polys") or []
    texts = data.get("rec_texts") or []
    scores = data.get("rec_scores") or []
    for i, text in enumerate(texts):
        if not str(text).strip():
            continue
        poly = polys[i] if i < len(polys) else None
        if poly is None:
            lines.append(f"- (?, ?) {text!r}")
            continue
        cx, cy = _poly_center(poly, scale_x=scale_x, scale_y=scale_y)
        score = float(scores[i]) if i < len(scores) else 0.0
        lines.append(f"- ({cx}, {cy}) {text!r} (置信度: {score:.2f})")
    return lines


def _format_v2_result(result, *, scale_x: float, scale_y: float) -> list[str]:
    lines: list[str] = []
    if not result or not result[0]:
        return lines
    for line in result[0]:
        box, (text, score) = line
        cx, cy = _poly_center(box, scale_x=scale_x, scale_y=scale_y)
        lines.append(f"- ({cx}, {cy}) {text!r} (置信度: {score:.2f})")
    return lines


def warmup_ocr() -> None:
    """可选预热：加载模型。不跑完整屏推理以免拖慢开局。"""
    get_ocr_instance()
    logger.info("PaddleOCR 模型已加载（warmup）")


def extract_text_with_bounds(image_path: Path | str) -> str:
    """
    对指定图片进行 OCR，返回带中心坐标的文本摘要（原图分辨率坐标）。
    兼容 PaddleOCR 2.x（ocr.ocr）与 3.x（predict）。
    """
    src = Path(image_path)
    logger.debug("OCR 开始: %s", src.name)
    ocr = get_ocr_instance()
    prepared = _prepare_image_for_ocr(src)
    infer_path = str(prepared.path)
    sx, sy = prepared.scale_x, prepared.scale_y

    t0 = time.perf_counter()
    try:
        if _PADDLEOCR_V3:
            results = ocr.predict(infer_path)
            lines: list[str] = []
            for item in results or []:
                lines.extend(_format_v3_result(item, scale_x=sx, scale_y=sy))
        else:
            lines = _format_v2_result(ocr.ocr(infer_path, cls=False), scale_x=sx, scale_y=sy)
    except NotImplementedError as e:
        logger.exception("PaddleOCR 识别失败（多为 Windows oneDNN/PIR 兼容问题）: %s", src)
        return (
            "[OCR 识别失败] Paddle CPU 推理与 oneDNN 不兼容。"
            "已在代码中设置 enable_mkldnn=False；若仍失败可尝试降级 paddlepaddle 至 3.2.x。"
            f" 详情: {e}"
        )
    except Exception as e:
        logger.exception("PaddleOCR 识别失败: %s", src)
        return f"[OCR 识别失败] {e}"
    finally:
        if prepared.temp_path is not None and prepared.temp_path.is_file():
            try:
                prepared.temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    elapsed = time.perf_counter() - t0
    logger.info(
        "OCR 完成 %.2fs profile=%s src=%s infer=%sx%s",
        elapsed,
        _OCR_CONFIG.model_profile,
        src.name,
        prepared.path.name,
        f" scale={sx:.2f}" if sx != 1.0 else "",
    )

    if not lines:
        return "[OCR] 未识别到任何文字"
    return "\n".join(lines)
