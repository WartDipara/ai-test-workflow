from __future__ import annotations

import logging
import os
import re
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


def _prepare_image_for_ocr(
    image_path: Path,
    *,
    device_w: int | None = None,
    device_h: int | None = None,
) -> _PreparedImage:
    """必要时缩小截图以加速 OCR；返回用于推理的路径与坐标缩放比。

    当提供 device_w/device_h 时，坐标映射到设备逻辑像素（adb input tap 坐标空间）；
    否则映射到截图像素空间（向后兼容）。

    自动处理横竖屏方向不匹配：截图尺寸可能反映物理像素（含密度倍率），
    或方向与 wm_size 不一致（横屏游戏截图宽高与设备逻辑像素宽高交换）。
    """
    from PIL import Image

    max_w = _OCR_CONFIG.max_image_width
    src = image_path.resolve()
    with Image.open(src) as im:
        im = im.convert("RGB")
        ow, oh = im.size

        # touch_size() 已经包含旋转补偿，直接使用
        if device_w and device_h:
            logger.info("OCR dims: src=%dx%d device=%dx%d", ow, oh, device_w, device_h)

        if ow <= max_w:
            sx = 1.0
            sy = 1.0
            if device_w:
                sx = device_w / ow
                sy = device_h / oh if device_h else 1.0
            return _PreparedImage(path=src, scale_x=sx, scale_y=sy, temp_path=None)
        nw = max_w
        nh = max(1, int(oh * nw / ow))
        resized = im.resize((nw, nh), Image.Resampling.LANCZOS)
        tmp = src.parent / f"{src.stem}_ocr_{nw}w.png"
        resized.save(tmp, format="PNG")
        if device_w:
            scale_x = device_w / nw
            scale_y = device_h / nh if device_h else 1.0
        else:
            scale_x = ow / nw
            scale_y = oh / nh
        return _PreparedImage(
            path=tmp,
            scale_x=scale_x,
            scale_y=scale_y,
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


def extract_text_with_bounds(
    image_path: Path | str,
    *,
    device_w: int | None = None,
    device_h: int | None = None,
) -> str:
    """
    对指定图片进行 OCR，返回带中心坐标的文本摘要。
    当 device_w/device_h 提供时，坐标映射到设备逻辑像素（adb input tap 坐标空间）。
    """
    src = Path(image_path)
    logger.debug("OCR 开始: %s", src.name)
    ocr = get_ocr_instance()
    prepared = _prepare_image_for_ocr(src, device_w=device_w, device_h=device_h)
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


_OCR_LINE_RE = re.compile(
    r"^- \((\d+),\s*(\d+)\)\s+(.+)$",
)


@dataclass(frozen=True)
class OcrLine:
    x: int
    y: int
    text: str


@dataclass(frozen=True)
class OcrBbox:
    text: str
    cx: int
    cy: int
    x1: int
    y1: int
    x2: int
    y2: int


def _v3_to_bboxes(data, *, scale_x: float, scale_y: float) -> list[OcrBbox]:
    polys = data.get("rec_polys") or data.get("dt_polys") or []
    texts = data.get("rec_texts") or []
    result: list[OcrBbox] = []
    for i, text in enumerate(texts):
        if not str(text).strip():
            continue
        poly = polys[i] if i < len(polys) else None
        if poly is None:
            continue
        xs = [float(p[0]) * scale_x for p in poly]
        ys = [float(p[1]) * scale_y for p in poly]
        if not xs:
            continue
        x1, y1, x2, y2 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        result.append(OcrBbox(text=str(text), cx=cx, cy=cy, x1=x1, y1=y1, x2=x2, y2=y2))
    return result


def _v2_to_bboxes(result, *, scale_x: float, scale_y: float) -> list[OcrBbox]:
    if not result or not result[0]:
        return []
    bboxes: list[OcrBbox] = []
    for line in result[0]:
        box, (text, _score) = line
        xs = [float(p[0]) * scale_x for p in box]
        ys = [float(p[1]) * scale_y for p in box]
        if not xs:
            continue
        x1, y1, x2, y2 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        bboxes.append(OcrBbox(text=str(text), cx=cx, cy=cy, x1=x1, y1=y1, x2=x2, y2=y2))
    return bboxes


def extract_text_with_bbox(
    image_path: Path | str,
    *,
    device_w: int | None = None,
    device_h: int | None = None,
) -> list[OcrBbox]:
    src = Path(image_path)
    ocr = get_ocr_instance()
    prepared = _prepare_image_for_ocr(src, device_w=device_w, device_h=device_h)
    infer_path = str(prepared.path)
    sx, sy = prepared.scale_x, prepared.scale_y
    try:
        if _PADDLEOCR_V3:
            results = ocr.predict(infer_path)
            bboxes: list[OcrBbox] = []
            for item in results or []:
                bboxes.extend(_v3_to_bboxes(item, scale_x=sx, scale_y=sy))
        else:
            bboxes = _v2_to_bboxes(ocr.ocr(infer_path, cls=False), scale_x=sx, scale_y=sy)
        return bboxes
    except Exception:
        return []
    finally:
        if prepared.temp_path is not None and prepared.temp_path.is_file():
            try:
                prepared.temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def is_screencap_mostly_black(
    image_path: Path | str,
    *,
    dark_threshold: int = 28,
    dark_ratio: float = 0.88,
    sample_max: int = 96_000,
) -> bool:
    """
    判断截屏是否主要为黑屏（安全键盘 / 密码框焦点时常见）。
    用于避免把黑屏 OCR 误判为「游戏卡死」。
    """
    from PIL import Image

    path = Path(image_path)
    if not path.is_file():
        return False
    try:
        with Image.open(path) as im:
            im = im.convert("L")
            w, h = im.size
            step = max(1, int((w * h / sample_max) ** 0.5))
            pixels = list(im.resize((max(1, w // step), max(1, h // step))).getdata())
    except Exception:
        return False
    if not pixels:
        return False
    dark = sum(1 for p in pixels if p <= dark_threshold)
    return (dark / len(pixels)) >= dark_ratio


def parse_ocr_lines(ocr_body: str) -> list[OcrLine]:
    """解析 extract_text_with_bounds 输出的行列表。"""
    lines: list[OcrLine] = []
    for raw in (ocr_body or "").splitlines():
        m = _OCR_LINE_RE.match(raw.strip())
        if not m:
            continue
        text = m.group(3)
        text = re.sub(r"\s*\(置信度:.*\)\s*$", "", text).strip()
        lines.append(OcrLine(x=int(m.group(1)), y=int(m.group(2)), text=text))
    return lines


def format_device_ocr_for_executor(
    ocr_body: str,
    *,
    screen_height: int,
    keyboard_min_y_ratio: float = 0.58,
) -> str:
    """
    标注 OCR 来自设备截屏，并将 y >= 阈值 的行标为输入法/安全键盘区（勿当登录按钮）。
    """
    h = max(1, int(screen_height))
    cutoff = int(h * float(keyboard_min_y_ratio))
    ui_lines: list[str] = []
    ime_lines: list[str] = []
    other: list[str] = []

    _dialog_btn = re.compile(
        r"(同意|不同意|^(登录|立即登录)$|确认|确定|取消|进入游戏|"
        r"^login$|forgot\s*password)",
        re.IGNORECASE,
    )
    _compound_login = re.compile(
        r"login\s*/\s*sign|sign\s*up\s*with|login/sign",
        re.IGNORECASE,
    )

    for line in (ocr_body or "").splitlines():
        m = _OCR_LINE_RE.match(line.strip())
        if not m:
            other.append(line)
            continue
        y = int(m.group(2))
        text = m.group(3)
        # 主 Login/登录 可落在 cutoff 以下；复合入口与 IME 键仍归 keyboard 区
        is_primary_login = bool(_dialog_btn.search(text.strip()))
        is_compound = bool(_compound_login.search(text))
        if y >= cutoff and not is_primary_login:
            ime_lines.append(line)
        elif y >= cutoff and is_compound:
            ime_lines.append(line)
        else:
            ui_lines.append(line)

    parts = [
        f"[Device live screencap OCR] screen_height={h}px; "
        f"UI region y<{cutoff} | keyboard/IME y>={cutoff} (do NOT tap IME keys for Login)",
    ]
    parts.append(f"=== Game UI (y < {cutoff}) ===")
    parts.append("\n".join(ui_lines) if ui_lines else "(no text)")
    if ime_lines:
        parts.append(f"=== Keyboard / IME only (y >= {cutoff}) — ignore for Login ===")
        parts.append("\n".join(ime_lines))
    if other:
        parts.append("=== Other ===")
        parts.append("\n".join(other))
    return "\n".join(parts)
