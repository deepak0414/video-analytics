"""Color stub detector — deterministic, dependency-free, for the test suite.

"Detects" a requested class when it is a named color: pixels near that palette
color form the detection mask; the bbox is the mask's extent. Pairs with
media.synth.write_box_video so the detect -> store -> count path is testable
offline with assertable boxes. YOLO-World provides real detection.
"""
from __future__ import annotations

from typing import List, Sequence

import numpy as np
from PIL import Image

from va.contracts.detection import Detection

_PALETTE = {
    "red": (220, 30, 30), "green": (30, 180, 30), "blue": (30, 30, 220),
    "yellow": (230, 220, 40), "white": (240, 240, 240), "black": (15, 15, 15),
}
_DIST = 60.0       # max RGB distance for a pixel to count as the color
_MIN_AREA = 0.002  # min mask fraction to report a detection


class ColorDetector:
    def detect(
        self, images: Sequence[Image.Image], classes: Sequence[str]
    ) -> List[List[Detection]]:
        out: List[List[Detection]] = []
        for img in images:
            arr = np.asarray(img.convert("RGB"), dtype=np.float32)
            h, w = arr.shape[:2]
            dets: List[Detection] = []
            for cls in classes:
                rgb = _PALETTE.get(cls.lower())
                if rgb is None:
                    continue
                mask = np.linalg.norm(arr - np.array(rgb, np.float32), axis=2) < _DIST
                frac = float(mask.mean())
                if frac < _MIN_AREA:
                    continue
                ys, xs = np.nonzero(mask)
                dets.append(Detection(
                    object_class=cls.lower(),
                    confidence=min(1.0, 0.5 + frac),
                    bbox_x=float(xs.min()) / w, bbox_y=float(ys.min()) / h,
                    bbox_w=float(xs.max() - xs.min() + 1) / w,
                    bbox_h=float(ys.max() - ys.min() + 1) / h,
                ))
            out.append(dets)
        return out
