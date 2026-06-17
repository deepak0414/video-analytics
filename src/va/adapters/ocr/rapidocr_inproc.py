"""Real Role-10 backend: RapidOCR (PP-OCR det+rec models on onnxruntime).

Why not PaddleOCR itself (plan.md S6.1 originally named it): paddlepaddle 3.2's
inference engine SEGFAULTS at predictor init on this aarch64 box (PIR parameter
loading; reproduced with PP-OCRv5/v6, MKLDNN on and off). RapidOCR runs the same
PP-OCR models exported to ONNX on onnxruntime, which is solid on aarch64 — same
recognition lineage, working runtime. Measured on the cobra short: the EN mobile
rec model reads burned-in captions cleanly ("bought a cobra" @0.92).

Samples frames at a low fps (text persists on screen far longer than one frame)
and collapses consecutive sightings of the same normalized text into one row per
appearance (a billboard visible 10s-14s = one line at 10s; the same billboard
returning at 100s = a second line). Loaded once via the ModelManager; runs on
CPU (no VRAM contention with the VLM). Requires the `ocr` extra. Select via
config: ocr.model = rapidocr.
"""
from __future__ import annotations

from typing import Any, List

import numpy as np

from va.contracts.ocr import OcrLine
from va.media.frames import sample_frames
from va.runtime.manager import MANAGER


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


class RapidOCRReader:
    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.lang = str(load.get("lang", "en"))
        self.fps = float(load.get("fps", 1.0))
        self.min_confidence = float(load.get("min_confidence", 0.5))
        self._ocr = MANAGER.get(f"rapidocr::{self.lang}", self._build)

    def _build(self):
        from rapidocr import LangRec, RapidOCR  # deferred heavy import

        # The default CH rec model garbles English spacing ("boughit acobra");
        # the language-specific model is required for usable word-level search.
        lang = getattr(LangRec, self.lang.upper(), LangRec.EN)
        return RapidOCR(params={"Rec.lang_type": lang})

    def _read_frame(self, img) -> List[OcrLine]:
        arr = np.asarray(img.convert("RGB"))[:, :, ::-1].copy()  # BGR, cv2 convention
        h, w = arr.shape[:2]
        result = self._ocr(arr)
        texts = result.txts or ()
        scores = result.scores or (1.0,) * len(texts)
        boxes = result.boxes if result.boxes is not None else [None] * len(texts)

        lines: List[OcrLine] = []
        for text, score, box in zip(texts, scores, boxes):
            if not text.strip() or float(score) < self.min_confidence:
                continue
            kw: dict[str, float] = {}
            if box is not None:
                pts = np.asarray(box, dtype=np.float64)  # 4x2 quad, pixel coords
                x0, y0 = pts[:, 0].min(), pts[:, 1].min()
                x1, y1 = pts[:, 0].max(), pts[:, 1].max()
                kw = {
                    "bbox_x": max(0.0, x0 / w), "bbox_y": max(0.0, y0 / h),
                    "bbox_w": max(0.0, (x1 - x0) / w), "bbox_h": max(0.0, (y1 - y0) / h),
                }
            lines.append(OcrLine(text=text.strip(), confidence=min(1.0, float(score)), **kw))
        return lines

    def read(self, media_path: str) -> List[OcrLine]:
        out: List[OcrLine] = []
        last_seen: dict[str, float] = {}  # normalized text -> last sighting ts
        gap = 2.0 / self.fps  # one missed sample tolerated before a new appearance
        for ts, img in sample_frames(media_path, fps=self.fps):
            for line in self._read_frame(img):
                key = _normalize(line.text)
                prev = last_seen.get(key)
                if prev is None or (ts - prev) > gap:
                    out.append(line.model_copy(update={"timestamp": ts}))
                last_seen[key] = ts
        return out
