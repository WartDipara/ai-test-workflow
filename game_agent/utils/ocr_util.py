from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from game_agent.models.settings import OcrSection
from game_agent.i18n import Concept, compile_lexicon_pattern

logger = logging.getLogger(__name__)


def _configure_paddle_runtime() -> None:
    """Windows + Paddle 3.x CPU 默认 oneDNN 可能触发 PIR 转换 NotImplementedError。"""
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")

_OCR_CONFIG: OcrSection = OcrSection()
_FRAME_CACHE: dict[tuple, tuple[str, list[OcrBbox]]] = {}
_FRAME_CACHE_MAX = 16


def configure_ocr(cfg: OcrSection, *, worker_key: str | None = None) -> None:
    """由 Controller 在 run 开始时注入；切换 profile 会重置已加载模型。"""
    global _OCR_CONFIG
    from game_agent.utils.ocr_worker import configure_ocr_worker

    _OCR_CONFIG = cfg
    configure_ocr_worker(cfg, worker_key=worker_key)
    clear_ocr_frame_cache()


def clear_ocr_frame_cache() -> None:
    _FRAME_CACHE.clear()


def get_ocr_instance(*, worker_key: str | None = None):
    """懒加载 PaddleOCR；经 OcrWorker 串行化。"""
    from game_agent.utils.ocr_worker import get_ocr_worker

    worker = get_ocr_worker(worker_key=worker_key)
    return worker.submit(worker.get_ocr_instance)


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
    max_image_width: int | None = None,
) -> _PreparedImage:
    from PIL import Image

    max_w = max_image_width if max_image_width is not None else _OCR_CONFIG.max_image_width
    src = image_path.resolve()
    with Image.open(src) as im:
        im = im.convert("RGB")
        ow, oh = im.size

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


def warmup_ocr(*, worker_key: str | None = None) -> None:
    """可选预热：加载模型。不跑完整屏推理以免拖慢开局。"""
    get_ocr_instance(worker_key=worker_key)
    logger.info("PaddleOCR model loaded (warmup)")


def _frame_cache_key(
    image_path: Path,
    *,
    device_w: int | None,
    device_h: int | None,
    worker_key: str | None,
) -> tuple:
    from game_agent.utils.ocr_worker import resolve_ocr_worker_key

    try:
        mtime = image_path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return (
        resolve_ocr_worker_key(worker_key),
        str(image_path.resolve()),
        mtime,
        device_w,
        device_h,
        _OCR_CONFIG.model_profile,
        _OCR_CONFIG.max_image_width,
        _OCR_CONFIG.device_policy,
        _OCR_CONFIG.gpu_id,
    )


def _infer_ocr_frame(
    image_path: Path | str,
    *,
    device_w: int | None = None,
    device_h: int | None = None,
    worker_key: str | None = None,
) -> tuple[str, list[OcrBbox]]:
    """单次 predict，同时返回 summary 与 bboxes（仅在 OcrWorker 线程调用）。"""
    from game_agent.utils.ocr_worker import get_ocr_worker

    src = Path(image_path)
    logger.debug("OCR start: %s", src.name)
    worker = get_ocr_worker(worker_key=worker_key)
    ocr = worker.get_ocr_instance()
    prepared = _prepare_image_for_ocr(src, device_w=device_w, device_h=device_h)
    infer_path = str(prepared.path)
    sx, sy = prepared.scale_x, prepared.scale_y
    paddle_v3 = worker.paddle_v3

    t0 = time.perf_counter()
    try:
        if paddle_v3:
            results = _predict_with_device_fallback(worker, ocr, infer_path)
            lines: list[str] = []
            bboxes: list[OcrBbox] = []
            for item in results or []:
                lines.extend(_format_v3_result(item, scale_x=sx, scale_y=sy))
                bboxes.extend(_v3_to_bboxes(item, scale_x=sx, scale_y=sy))
        else:
            raw = ocr.ocr(infer_path, cls=False)
            lines = _format_v2_result(raw, scale_x=sx, scale_y=sy)
            bboxes = _v2_to_bboxes(raw, scale_x=sx, scale_y=sy)
    except NotImplementedError as e:
        logger.exception("PaddleOCR failed (often Windows oneDNN/PIR): %s", src)
        err = (
            "[OCR failed] Paddle CPU inference incompatible with oneDNN."
            "enable_mkldnn=False set in code; try downgrading paddlepaddle to 3.2.x if still fails."
            f" details: {e}"
        )
        return err, []
    except Exception as e:
        logger.exception("PaddleOCR failed: %s", src)
        return f"[OCR failed] {e}", []
    finally:
        if prepared.temp_path is not None and prepared.temp_path.is_file():
            try:
                prepared.temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    elapsed = time.perf_counter() - t0
    device_label = worker.effective_device
    logger.info(
        "OCR done %.2fs device=%s profile=%s src=%s infer=%sx%s",
        elapsed,
        device_label,
        _OCR_CONFIG.model_profile,
        src.name,
        prepared.path.name,
        f" scale={sx:.2f}" if sx != 1.0 else "",
    )

    if not lines:
        return "[OCR] no text recognized", bboxes
    return "\n".join(lines), bboxes


def _predict_with_device_fallback(worker, ocr, infer_path: str):
    """GPU 推理失败且允许降级时，切换 CPU 后重试一次。"""
    try:
        return ocr.predict(infer_path)
    except Exception as exc:
        cfg = worker.cfg
        if (
            worker.effective_device.startswith("gpu")
            and cfg.allow_gpu_fallback_to_cpu
        ):
            worker.force_cpu_fallback(str(exc))
            ocr = worker.get_ocr_instance()
            return ocr.predict(infer_path)
        raise


def run_ocr_frame(
    image_path: Path | str,
    *,
    device_w: int | None = None,
    device_h: int | None = None,
    worker_key: str | None = None,
) -> tuple[str, list[OcrBbox]]:
    """
    对指定图片进行一次 OCR 推理，返回 (summary, bboxes)。
    同帧重复调用经帧缓存与 OcrWorker 串行化，不会重复 predict。
    """
    src = Path(image_path)
    cache_key = _frame_cache_key(src, device_w=device_w, device_h=device_h, worker_key=worker_key)
    cached = _FRAME_CACHE.get(cache_key)
    if cached is not None:
        return cached

    from game_agent.utils.ocr_worker import get_ocr_worker

    worker = get_ocr_worker(worker_key=worker_key)
    result = worker.submit(
        _infer_ocr_frame,
        src,
        device_w=device_w,
        device_h=device_h,
        worker_key=worker_key,
    )
    if len(_FRAME_CACHE) >= _FRAME_CACHE_MAX:
        _FRAME_CACHE.clear()
    _FRAME_CACHE[cache_key] = result
    return result


def extract_text_with_bounds(
    image_path: Path | str,
    *,
    device_w: int | None = None,
    device_h: int | None = None,
    worker_key: str | None = None,
) -> str:
    summary, _ = run_ocr_frame(
        image_path,
        device_w=device_w,
        device_h=device_h,
        worker_key=worker_key,
    )
    return summary


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


def serialize_bboxes(bboxes: list[OcrBbox]) -> list[dict[str, int | str]]:
    return [asdict(b) for b in bboxes]


def deserialize_bboxes(raw: list[dict[str, int | str]] | None) -> list[OcrBbox]:
    if not raw:
        return []
    return [OcrBbox(**item) for item in raw]


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
    worker_key: str | None = None,
) -> list[OcrBbox]:
    _, bboxes = run_ocr_frame(
        image_path,
        device_w=device_w,
        device_h=device_h,
        worker_key=worker_key,
    )
    return bboxes


def is_screencap_mostly_black(
    image_path: Path | str,
    *,
    dark_threshold: int = 28,
    dark_ratio: float = 0.88,
    sample_max: int = 96_000,
) -> bool:
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
    h = max(1, int(screen_height))
    cutoff = int(h * float(keyboard_min_y_ratio))
    ui_lines: list[str] = []
    ime_lines: list[str] = []
    other: list[str] = []

    _dialog_btn = compile_lexicon_pattern(
        Concept.AGREE,
        Concept.CANCEL,
        Concept.PRIVACY_DISAGREE,
        Concept.LOGIN_BUTTON,
        Concept.CONFIRM,
        Concept.ENTER_GAME,
        Concept.FORGOT_PASSWORD,
    )
    _compound_login = compile_lexicon_pattern(Concept.COMPOUND_LOGIN)

    for line in (ocr_body or "").splitlines():
        m = _OCR_LINE_RE.match(line.strip())
        if not m:
            other.append(line)
            continue
        y = int(m.group(2))
        text = m.group(3)
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
        f"UI region y<{cutoff} | lower-screen/IME-risk y>={cutoff} "
        "(may include background game CTA — check LoginStageProbe)",
    ]
    parts.append(f"=== Game UI (y < {cutoff}) ===")
    parts.append("\n".join(ui_lines) if ui_lines else "(no text)")
    if ime_lines:
        parts.append(
            f"=== Lower-screen / IME-risk (y >= {cutoff}) — "
            "not always keyboard; may be background enter-game CTA ===",
        )
        parts.append("\n".join(ime_lines))
    if other:
        parts.append("=== Other ===")
        parts.append("\n".join(other))
    return "\n".join(parts)
