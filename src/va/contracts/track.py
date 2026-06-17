"""Track contracts — Role 6 output; rows of the `object_tracks` table.

A track is one distinct object instance followed across frames. Tracks are what
make "how many distinct X" answerable (Role 5 detections alone only give frame
appearances).
"""
from __future__ import annotations

from typing import List
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from va.contracts.detection import Detection


class ObjectTrack(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    video_id: UUID
    object_class: str
    track_confidence: float = 0.0   # max detection confidence in the track
    first_seen: float = 0.0
    last_seen: float = 0.0
    frame_count: int = 0


class TrackingResult(BaseModel):
    """Tracker output: the tracks plus the same detections with track_id set."""

    tracks: List[ObjectTrack] = Field(default_factory=list)
    detections: List[Detection] = Field(default_factory=list)
