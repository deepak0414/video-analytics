"""Video catalog contracts — mirror the `videos` table in the architecture doc.

These types are the boundary between the source-acquisition layer and the
catalog store. `ResolvedVideo` is what a `VideoSource` produces; `Video` is the
catalog row.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SourceType(str, enum.Enum):
    youtube = "youtube"
    local = "local"
    nvr_recorded = "nvr_recorded"  # a wall-clock window pulled from an NVR (WS4.c)
    # extensible later: url, s3, …


class IngestStatus(str, enum.Enum):
    pending = "pending"
    fetching = "fetching"
    processing = "processing"
    done = "done"
    failed = "failed"
    # Deliberately excluded from analytics: the media is contaminated / was
    # rejected as not-the-requested-footage (e.g. a foreign sub-stream flagged by
    # the .va-24h integrity repair). A terminal state, DISTINCT from `failed` (an
    # ingest *error*, retryable) and from `done` (a completed, searchable ingest).
    # Enforcement today is by STATUS + PURGE, not by status alone: this value keeps
    # the clip out of the active/processable set (dedup no-op, and `va stale` /
    # `va reprocess` skip it), but the read paths (query/caption/ocr/… gather) do
    # NOT yet filter on ingest_status, so any role rows/vector shards left behind
    # stay searchable. The clip is only truly dark once its rows/shards are also
    # purged — which the .va-24h repair did. The NVR delivery verifier
    # (`sources/verify.py`, WS-4) that fails-closed on a bad pull is the intended
    # future writer; wiring it (and/or gating the read paths on status) is future
    # work. Set out-of-band by data repair today.
    quarantined = "quarantined"


class VideoMetadata(BaseModel):
    """Probed media properties (filled in once the file is local)."""

    title: Optional[str] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None
    resolution: Optional[str] = None  # e.g. "640x360"
    has_audio: Optional[bool] = None


class ResolvedVideo(BaseModel):
    """What a VideoSource returns: enough to dedup + locate the file locally.

    Chunk placement (WS4.c): a source that knows WHERE the footage sits on the
    wall clock and WHICH camera recorded it (the NVR chunk source) declares it
    here, and ingest attaches both to the catalog row BEFORE any role runs —
    Role 1's motion-episodes backend consumes `start_epoch` mid-ingest, and the
    row must survive a hard kill with its placement already recorded. None for
    placeless sources (YouTube, plain files).
    """

    source_type: SourceType
    source_uri: str  # canonical input (full URL or original path)
    source_key: str  # dedup key: youtube video_id, or sha256 for local
    local_path: Optional[str] = None  # set once fetched/copied to disk
    metadata: VideoMetadata = Field(default_factory=VideoMetadata)
    start_epoch: Optional[float] = None    # UTC epoch of the chunk's t=0
    camera: Optional["Camera"] = None      # the recording camera (row ensured at ingest)


class Camera(BaseModel):
    """A fixed camera/stream (the `cameras` table, WS-3). Videos ("chunks") of an
    A-LSSRVF reference one via `camera_id`; a camera's chunks form a collection."""

    id: str
    name: str
    source_ref: Optional[str] = None   # how to reach the feed (RTSP URL / NVR channel)
    location: Optional[str] = None     # human spatial hint ("front door"); WS-5 topology input
    # WS6.b: catch-up watermark — UTC epoch up to which this camera's motion
    # windows have been pulled+ingested. NULL = never watched.
    last_processed_epoch: Optional[float] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Video(BaseModel):
    """A catalog row (the `videos` table)."""

    id: UUID = Field(default_factory=uuid4)
    source_type: SourceType
    source_uri: str
    source_key: str
    local_path: Optional[str] = None
    title: Optional[str] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None
    resolution: Optional[str] = None
    has_audio: Optional[bool] = None
    ingest_status: IngestStatus = IngestStatus.pending
    ingest_error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    fetched_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    # Trace run_id of the ingest that last processed this video (ingest<->query
    # trace link). None when it was ingested with tracing off.
    last_ingest_run_id: Optional[str] = None
    # Footage profile the video was ingested under (WS-2). None = ingested
    # before the profile layer existed.
    profile: Optional[str] = None
    # Camera this chunk came from (WS-3, A-LSSRVF). None = standalone video (A-EV).
    camera_id: Optional[str] = None
    # Absolute UTC epoch seconds of this chunk's t=0 (dual time model, plan §4).
    # None = relative-only. Every stored timestamp stays video-relative;
    # absolute = start_epoch + relative.
    start_epoch: Optional[float] = None

    @classmethod
    def from_resolved(cls, r: ResolvedVideo) -> "Video":
        m = r.metadata
        return cls(
            source_type=r.source_type,
            source_uri=r.source_uri,
            source_key=r.source_key,
            local_path=r.local_path,
            title=m.title,
            duration_seconds=m.duration_seconds,
            fps=m.fps,
            resolution=m.resolution,
            has_audio=m.has_audio,
        )


# ResolvedVideo references Camera (defined later in this module) by forward ref.
ResolvedVideo.model_rebuild()
