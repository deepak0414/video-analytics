"""Role 1 — Scene Boundary Detector (the contract).

Splits a video into content-coherent segments. Output matches the architecture
doc: a list of (start_time, end_time) spans in seconds, covering the video in
order. Backends (histogram stub, PySceneDetect, …) are interchangeable.
"""
from __future__ import annotations

from typing import List, Protocol, Tuple, runtime_checkable

# (start_time, end_time) in seconds
SceneSpan = Tuple[float, float]


@runtime_checkable
class SceneDetector(Protocol):
    def detect(self, video_path: str) -> List[SceneSpan]:
        """Return ordered, non-overlapping (start, end) spans covering the video."""
        ...
