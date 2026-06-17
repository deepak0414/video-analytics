"""Sidecar stub OCR — deterministic, dependency-free, for the test suite.

Real OCR needs a model + frames that actually contain rendered text; our
synthetic test clips are flat color. So this backend reads a sidecar file placed
next to the video (`<video>.ocr.json`), letting the full ingest→store→search
path be tested deterministically. If no sidecar exists, it returns no text.

Sidecar format: {"lines": [{"timestamp": 1.0, "text": "COORS LIGHT",
                            "bbox_x": 0.1, "bbox_y": 0.1, "bbox_w": 0.3, "bbox_h": 0.1}, ...]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from va.contracts.ocr import OcrLine


def sidecar_path(media_path: str) -> Path:
    return Path(media_path).with_suffix(".ocr.json")


class SidecarOCR:
    def read(self, media_path: str) -> List[OcrLine]:
        path = sidecar_path(media_path)
        if not path.exists():
            return []
        doc = json.loads(path.read_text())
        return [OcrLine(**ln) for ln in doc.get("lines", [])]
