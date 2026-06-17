"""Synthetic colored-segment video generator.

Used by tests and as a stand-in fixture: produces a video where each listed
segment is a solid color for a number of seconds. Pairs with the color-aware
hash embedder so retrieval can be asserted deterministically.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence, Tuple

import imageio.v2 as imageio
import numpy as np

# (color_name, rgb, seconds)
Segment = Tuple[str, Tuple[int, int, int], float]


def write_box_video(
    path: str | Path,
    bg_rgb: Tuple[int, int, int],
    box_rgb: Tuple[int, int, int],
    box_frac: Tuple[float, float, float, float],  # (x, y, w, h) as fractions
    seconds: float = 2.0,
    fps: int = 10,
    size: Tuple[int, int] = (64, 64),
) -> Path:
    """A clip with a solid colored rectangle on a background — gives the stub
    object detector something with an assertable bounding box."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    x0, y0 = int(box_frac[0] * w), int(box_frac[1] * h)
    x1, y1 = x0 + int(box_frac[2] * w), y0 + int(box_frac[3] * h)
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :] = bg_rgb
    frame[y0:y1, x0:x1] = box_rgb
    writer = imageio.get_writer(str(path), fps=fps, macro_block_size=None)
    try:
        for _ in range(int(round(seconds * fps))):
            writer.append_data(frame)
    finally:
        writer.close()
    return path


def write_boxes_video(
    path: str | Path,
    bg_rgb: Tuple[int, int, int],
    boxes: Sequence[dict],
    seconds: float = 3.0,
    fps: int = 10,
    size: Tuple[int, int] = (64, 64),
) -> Path:
    """A clip with multiple colored rectangles, each optionally drifting.

    boxes: [{"rgb": (r,g,b), "frac": (x,y,w,h), "drift": (dx,dy) per second}, ...]
    Used to test the tracker: distinct boxes should become distinct tracks even
    while moving (as long as consecutive samples overlap).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    writer = imageio.get_writer(str(path), fps=fps, macro_block_size=None)
    try:
        n_frames = int(round(seconds * fps))
        for i in range(n_frames):
            t = i / fps
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            frame[:, :] = bg_rgb
            for b in boxes:
                dx, dy = b.get("drift", (0.0, 0.0))
                fx, fy, fw, fh = b["frac"]
                x0 = int(min(max(fx + dx * t, 0.0), 1.0 - fw) * w)
                y0 = int(min(max(fy + dy * t, 0.0), 1.0 - fh) * h)
                frame[y0:y0 + int(fh * h), x0:x0 + int(fw * w)] = b["rgb"]
            writer.append_data(frame)
    finally:
        writer.close()
    return path


def write_color_video(
    path: str | Path,
    segments: Sequence[Segment],
    fps: int = 10,
    size: Tuple[int, int] = (64, 64),
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    writer = imageio.get_writer(str(path), fps=fps, macro_block_size=None)
    try:
        for _name, rgb, seconds in segments:
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            frame[:, :] = rgb
            for _ in range(int(round(seconds * fps))):
                writer.append_data(frame)
    finally:
        writer.close()
    return path
