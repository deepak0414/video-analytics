import numpy as np
import pytest
from uuid import uuid4

from va.contracts.video import (
    IngestStatus,
    ResolvedVideo,
    SourceType,
    Video,
    VideoMetadata,
)
from va.contracts.embedding import FrameEmbedding, SearchHit


def test_resolved_video_roundtrip():
    r = ResolvedVideo(
        source_type=SourceType.youtube,
        source_uri="https://youtu.be/abc123DEFGH",
        source_key="abc123DEFGH",
        local_path="/tmp/abc.mp4",
        metadata=VideoMetadata(title="t", duration_seconds=12.0, fps=30, resolution="640x360", has_audio=True),
    )
    assert ResolvedVideo.model_validate_json(r.model_dump_json()) == r


def test_video_from_resolved_carries_fields_and_defaults():
    r = ResolvedVideo(
        source_type=SourceType.local,
        source_uri="/data/clip.mp4",
        source_key="sha256:deadbeef",
        local_path="/data/clip.mp4",
        metadata=VideoMetadata(duration_seconds=5.0, fps=24),
    )
    v = Video.from_resolved(r)
    assert v.source_key == "sha256:deadbeef"
    assert v.duration_seconds == 5.0 and v.fps == 24
    assert v.ingest_status is IngestStatus.pending  # default
    assert v.id is not None and v.created_at is not None


def test_frame_embedding_validates_and_casts_dtype():
    fe = FrameEmbedding(video_id=uuid4(), timestamp=3.5, vector=np.ones(768, dtype=np.float64))
    assert fe.vector.dtype == np.float32 and fe.vector.shape == (768,)


def test_frame_embedding_rejects_2d_and_negative_ts():
    with pytest.raises(Exception):
        FrameEmbedding(video_id=uuid4(), timestamp=0.0, vector=np.ones((2, 768)))
    with pytest.raises(Exception):
        FrameEmbedding(video_id=uuid4(), timestamp=-1.0, vector=np.ones(768))


def test_search_hit_shape():
    h = SearchHit(video_id=uuid4(), source_uri="https://youtu.be/x", timestamp=2.0, score=0.9)
    assert h.score == 0.9
