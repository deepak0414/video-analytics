"""Frame sampling + media probing via imageio (bundled ffmpeg).

`sample_frames` yields (timestamp_seconds, PIL.Image) at a target fps by striding
over the decoded frames. `probe` returns the media properties we store in the
catalog.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Tuple

import imageio.v2 as imageio
from PIL import Image

from va.contracts.video import VideoMetadata


def probe(path: str | Path) -> VideoMetadata:
    reader = imageio.get_reader(str(path))
    meta = reader.get_meta_data()
    size = meta.get("size")  # (w, h)
    resolution = f"{size[0]}x{size[1]}" if size else None
    has_audio = None
    if "audio_codec" in meta:
        has_audio = bool(meta.get("audio_codec"))
    reader.close()
    return VideoMetadata(
        duration_seconds=meta.get("duration"),
        fps=meta.get("fps"),
        resolution=resolution,
        has_audio=has_audio,
    )


def frames_at(path: str | Path, timestamps: list[float]) -> list[Image.Image]:
    """Grab one frame at each requested timestamp (seconds). Used for keyframes."""
    reader = imageio.get_reader(str(path))
    meta = reader.get_meta_data()
    src_fps = meta.get("fps") or 30.0
    n_frames = meta.get("nframes")
    out: list[Image.Image] = []
    try:
        for ts in timestamps:
            idx = int(round(ts * src_fps))
            if isinstance(n_frames, int) and n_frames > 0:
                idx = min(idx, n_frames - 1)
            reader.set_image_index(max(0, idx))
            out.append(Image.fromarray(reader.get_next_data()))
    finally:
        reader.close()
    return out


def keyframes_for_spans(
    path: str | Path, spans: list[Tuple[float, float]], per_segment: int = 1
) -> list[list[Image.Image]]:
    """For each (start, end) span, return `per_segment` evenly-spaced keyframes."""
    timestamps: list[float] = []
    layout: list[list[int]] = []
    for (s, e) in spans:
        idxs = []
        for j in range(per_segment):
            frac = (j + 1) / (per_segment + 1)
            idxs.append(len(timestamps))
            timestamps.append(s + frac * (e - s))
        layout.append(idxs)
    frames = frames_at(path, timestamps)
    return [[frames[i] for i in idxs] for idxs in layout]


def sample_frames(path: str | Path, fps: float = 1.0) -> Iterator[Tuple[float, Image.Image]]:
    """Yield frames at ~`fps`. Always yields at least the first frame."""
    reader = imageio.get_reader(str(path))
    meta = reader.get_meta_data()
    src_fps = meta.get("fps") or 30.0
    stride = max(1, int(round(src_fps / fps))) if fps > 0 else 1
    try:
        for idx, frame in enumerate(reader):
            if idx % stride == 0:
                ts = idx / src_fps
                yield ts, Image.fromarray(frame)
    finally:
        reader.close()
