"""Content-aware scene detector via color-histogram differences.

Default Role-1 backend: no extra dependencies (uses our imageio frame sampler +
numpy), deterministic, and runs offline — so it works in the pure test suite.
Samples frames at a low fps, compares consecutive color histograms, and cuts
where the difference exceeds a threshold. A heavier/sharper backend
(PySceneDetect) can sit behind the same SceneDetector Protocol.
"""
from __future__ import annotations

from typing import List

import numpy as np
from PIL import Image

from va.media.frames import probe, sample_frames
from va.roles.scene_detector import SceneSpan

_BINS = 16


def _histogram(img: Image.Image) -> np.ndarray:
    """Per-channel normalized RGB histogram (each channel block sums to 1)."""
    arr = np.asarray(img.convert("RGB").resize((64, 64)), dtype=np.float32)
    blocks = []
    for c in range(3):
        hist, _ = np.histogram(arr[:, :, c], bins=_BINS, range=(0, 255))
        total = hist.sum()
        blocks.append(hist / total if total > 0 else hist)
    return np.concatenate(blocks)


def _distance(h1: np.ndarray, h2: np.ndarray) -> float:
    """Histogram-intersection distance in [0, 1] averaged over the 3 channels."""
    return float(0.5 * np.abs(h1 - h2).sum() / 3.0)


class HistogramSceneDetector:
    def __init__(self, sample_fps: float = 3.0, threshold: float = 0.4, min_scene_len: float = 0.6):
        self.sample_fps = sample_fps
        self.threshold = threshold
        self.min_scene_len = min_scene_len

    def detect(self, video_path: str) -> List[SceneSpan]:
        frames = list(sample_frames(video_path, fps=self.sample_fps))
        meta = probe(video_path)
        duration = meta.duration_seconds
        if duration is None:
            duration = (frames[-1][0] + 1.0 / self.sample_fps) if frames else 0.0

        if len(frames) < 2:
            return [(0.0, float(duration))]

        hists = [(t, _histogram(img)) for t, img in frames]
        boundaries = [0.0]
        last_cut = 0.0
        for i in range(1, len(hists)):
            t, h = hists[i]
            if _distance(hists[i - 1][1], h) > self.threshold and (t - last_cut) >= self.min_scene_len:
                boundaries.append(t)
                last_cut = t

        spans: List[SceneSpan] = []
        for i, b in enumerate(boundaries):
            end = boundaries[i + 1] if i + 1 < len(boundaries) else float(duration)
            spans.append((float(b), float(end)))
        return spans
