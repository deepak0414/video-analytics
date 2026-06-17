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
    # extensible later: url, s3, …


class IngestStatus(str, enum.Enum):
    pending = "pending"
    fetching = "fetching"
    processing = "processing"
    done = "done"
    failed = "failed"


class VideoMetadata(BaseModel):
    """Probed media properties (filled in once the file is local)."""

    title: Optional[str] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None
    resolution: Optional[str] = None  # e.g. "640x360"
    has_audio: Optional[bool] = None


class ResolvedVideo(BaseModel):
    """What a VideoSource returns: enough to dedup + locate the file locally."""

    source_type: SourceType
    source_uri: str  # canonical input (full URL or original path)
    source_key: str  # dedup key: youtube video_id, or sha256 for local
    local_path: Optional[str] = None  # set once fetched/copied to disk
    metadata: VideoMetadata = Field(default_factory=VideoMetadata)


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
