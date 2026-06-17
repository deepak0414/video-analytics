"""Color stub captioner — deterministic, dependency-free, for the test suite.

A real VLM caption needs a model; our synthetic clips are solid colors. So this
backend captions a segment by the dominant color of its keyframe ("a red scene"),
letting the caption -> store -> search path be tested deterministically. The
Qwen2.5-VL backend provides real descriptions.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
from PIL import Image

_PALETTE = {
    "red": (220, 30, 30), "green": (30, 180, 30), "blue": (30, 30, 220),
    "yellow": (230, 220, 40), "cyan": (40, 210, 210), "magenta": (210, 40, 210),
    "orange": (240, 140, 30), "white": (240, 240, 240), "black": (15, 15, 15),
    "gray": (128, 128, 128),
}


def _dominant_color(img: Image.Image) -> str:
    mean = np.asarray(img.convert("RGB").resize((32, 32)), dtype=np.float32).reshape(-1, 3).mean(0)
    return min(_PALETTE, key=lambda n: float(((mean - np.array(_PALETTE[n])) ** 2).sum()))


class ColorCaptioner:
    def caption(self, images: Sequence[Image.Image], prompt: Optional[str] = None) -> str:
        if not images:
            return "an empty scene"
        return f"a {_dominant_color(images[0])} scene"
