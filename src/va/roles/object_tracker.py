"""Role 6 — Object Tracker (the contract).

Takes Role-5 detections grouped by frame timestamp and associates them into
persistent tracks (consistent IDs across frames). Backends (IoU association,
ByteTrack, SAM 2 later) are interchangeable.

Note: tracking quality depends on the detection sampling rate — at 1 fps a
fast-moving object may not overlap itself between samples. Acceptable for the
PoC; tracking at native fps is a later performance concern.
"""
from __future__ import annotations

from typing import List, Protocol, Sequence, Tuple, runtime_checkable
from uuid import UUID

from va.contracts.detection import Detection
from va.contracts.track import TrackingResult

# (timestamp, detections at that timestamp)
FrameDetections = Tuple[float, List[Detection]]


@runtime_checkable
class ObjectTracker(Protocol):
    def track(
        self, video_id: UUID, frames: Sequence[FrameDetections]
    ) -> TrackingResult:
        """Associate detections across frames; return tracks + detections with
        track_id filled."""
        ...
