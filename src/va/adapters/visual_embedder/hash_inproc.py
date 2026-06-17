"""Deterministic stub embedder — for tests/CI without downloading SigLIP.

It is *color-aware*: an image embeds to a vector derived from its dominant
named color, and text embeds via the same function after extracting a color
word. So a red frame and the query "red sports car" land on the same vector
(cosine ~1.0). This lets the full ingest→query pipeline be tested end-to-end
with a real, meaningful retrieval assertion — no model weights, no GPU.

It is NOT semantic beyond color; the SigLIP backend provides real semantics.
"""
from __future__ import annotations

import hashlib
from typing import Sequence

import numpy as np
from PIL import Image

# Small named palette (name -> RGB).
_PALETTE: dict[str, tuple[int, int, int]] = {
    "red": (220, 30, 30),
    "green": (30, 180, 30),
    "blue": (30, 30, 220),
    "yellow": (230, 220, 40),
    "cyan": (40, 210, 210),
    "magenta": (210, 40, 210),
    "orange": (240, 140, 30),
    "white": (240, 240, 240),
    "black": (15, 15, 15),
    "gray": (128, 128, 128),
}


def _key_to_vec(key: str, dim: int) -> np.ndarray:
    """Stable string -> unit vector. Same key always yields the same vector."""
    seed = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    n = np.linalg.norm(v)
    return v / (n or 1.0)


def _nearest_color(rgb: tuple[float, float, float]) -> str:
    r, g, b = rgb
    best, bestd = "black", float("inf")
    for name, (pr, pg, pb) in _PALETTE.items():
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if d < bestd:
            best, bestd = name, d
    return best


class HashEmbedder:
    def __init__(self, dim: int = 64):
        self.dim = dim

    def embed_image(self, images: Sequence[Image.Image]) -> np.ndarray:
        out = np.empty((len(images), self.dim), np.float32)
        for i, img in enumerate(images):
            arr = np.asarray(img.convert("RGB"), dtype=np.float32)
            mean = tuple(arr.reshape(-1, 3).mean(axis=0))
            out[i] = _key_to_vec(_nearest_color(mean), self.dim)
        return out

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        out = np.empty((len(texts), self.dim), np.float32)
        for i, text in enumerate(texts):
            words = text.lower().split()
            color = next((w for w in words if w in _PALETTE), None)
            # If a color word is present, match the color space; else hash the
            # whole phrase (won't match any frame -> low scores, as expected).
            out[i] = _key_to_vec(color if color else f"__text__:{text.lower()}", self.dim)
        return out
