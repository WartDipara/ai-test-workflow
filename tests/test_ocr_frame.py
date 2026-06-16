"""run_ocr_frame 单次推理与帧缓存。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from game_agent.utils.ocr_util import OcrBbox, clear_ocr_frame_cache, run_ocr_frame


def test_run_ocr_frame_single_predict(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"fake")

    bbox = OcrBbox(text="登录", cx=100, cy=200, x1=90, y1=190, x2=110, y2=210)
    infer_calls = 0

    def _fake_infer(image_path, *, device_w=None, device_h=None, worker_key=None):
        nonlocal infer_calls
        infer_calls += 1
        return "- (100, 200) '登录' (置信度: 0.99)", [bbox]

    clear_ocr_frame_cache()
    worker = MagicMock()
    worker.submit.side_effect = lambda fn, *a, **kw: fn(*a, **kw)

    with patch("game_agent.utils.ocr_worker.get_ocr_worker", return_value=worker):
        with patch("game_agent.utils.ocr_util._infer_ocr_frame", side_effect=_fake_infer):
            s1, b1 = run_ocr_frame(img, device_w=1080, device_h=2400)
            s2, b2 = run_ocr_frame(img, device_w=1080, device_h=2400)

    assert infer_calls == 1
    assert "登录" in s1
    assert b1 == b2


def test_extract_bounds_and_bbox_share_frame(tmp_path: Path) -> None:
    from game_agent.utils.ocr_util import extract_text_with_bbox, extract_text_with_bounds

    img = tmp_path / "frame.png"
    img.write_bytes(b"fake")
    infer_calls = 0

    def _fake_infer(image_path, *, device_w=None, device_h=None, worker_key=None):
        nonlocal infer_calls
        infer_calls += 1
        bbox = OcrBbox(text="区服", cx=50, cy=60, x1=40, y1=50, x2=60, y2=70)
        return "- (50, 60) '区服' (置信度: 0.90)", [bbox]

    clear_ocr_frame_cache()
    worker = MagicMock()
    worker.submit.side_effect = lambda fn, *a, **kw: fn(*a, **kw)

    with patch("game_agent.utils.ocr_worker.get_ocr_worker", return_value=worker):
        with patch("game_agent.utils.ocr_util._infer_ocr_frame", side_effect=_fake_infer):
            summary = extract_text_with_bounds(img)
            bboxes = extract_text_with_bbox(img)

    assert infer_calls == 1
    assert "区服" in summary
    assert len(bboxes) == 1
