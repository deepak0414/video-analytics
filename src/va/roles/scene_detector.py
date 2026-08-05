"""Role 1 — Scene Boundary Detector (the contract).

Splits a video into content-coherent segments. Output matches the architecture
doc: a list of (start_time, end_time) spans in seconds, covering the video in
order. Backends (histogram stub, PySceneDetect, motion-episodes, …) are
interchangeable.

`SceneContext` (WS4.b) is optional per-video context a caller MAY pass: purely
visual backends ignore it, while the motion-episodes backend needs the chunk's
absolute placement (`start_epoch`, plan §4 dual time model) and the camera's
source-native ref to ask a MotionSource what happened during the chunk. All
fields default to None so existing callers/backends are unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple, runtime_checkable

# (start_time, end_time) in seconds
SceneSpan = Tuple[float, float]


@dataclass(frozen=True)
class SceneContext:
    """Per-video context for backends that segment by more than pixels."""

    start_epoch: Optional[float] = None    # UTC epoch of t=0 (videos.start_epoch)
    camera_ref: Optional[str] = None       # source-native ref (cameras.source_ref)
    duration_seconds: Optional[float] = None  # None -> backend probes the file


@runtime_checkable
class SceneDetector(Protocol):
    def detect(
        self, video_path: str, context: Optional[SceneContext] = None
    ) -> List[SceneSpan]:
        """Return ordered, non-overlapping (start, end) spans.

        Coverage is BACKEND-DEPENDENT: visual backends (histogram,
        pyscenedetect) tile the whole video; motion-episodes returns only the
        motion windows — possibly NONE for a quiet chunk — and one full-span
        segment in its degraded modes (no start_epoch / source failure).
        Consumers must not assume the span union covers the video.
        """
        ...
