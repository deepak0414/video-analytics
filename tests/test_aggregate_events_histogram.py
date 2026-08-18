"""list_events + timeline_histogram (typed-query tier, TQ1.f).

Cross-check discipline: both ops route through the SAME selection path as
`count_objects`, so the list rows must be exactly the tracks behind the count
and the histogram bucket counts must sum to it. Ground truth is the same
hand-derived worksheet as the count tests: in-window cars e1 (abs W0), a1
(W0+3610), a2 (W0+3700), b1 (W1-10) -> hourly buckets 0:{e1}=1, 1:{a1,a2}=2,
11:{b1}=1, all others 0.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from va.contracts.aggregate import TimeWindow
from va.contracts.track import ObjectTrack
from va.contracts.video import Camera, IngestStatus, SourceType, Video
from va.pipeline.aggregate import (
    count_objects, list_events, timeline_histogram,
)
from va.pipeline.paths import Workspace
from va.storage.structured.cameras import CameraStore
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.tracks import TrackStore

# W0 = Aug-11 2026 00:00 PDT = 1786431600; W1 = 12:00 PDT = 1786474800.
W0 = 1786431600.0
W1 = 1786474800.0

WINDOW = TimeWindow(start=datetime(2026, 8, 11, 0, 0),
                    end=datetime(2026, 8, 11, 12, 0),
                    tz="America/Los_Angeles")


def _video(key, camera_id, start_epoch, duration=120.0):
    return Video(source_type=SourceType.local, source_uri=f"/{key}", source_key=key,
                 camera_id=camera_id, start_epoch=start_epoch,
                 duration_seconds=duration, ingest_status=IngestStatus.done)


def _track(video_id, cls, first_seen, frames=3):
    return ObjectTrack(id=uuid4(), video_id=video_id, object_class=cls,
                       track_confidence=0.9, first_seen=first_seen,
                       last_seen=first_seen + 5.0, frame_count=frames)


@pytest.fixture()
def workdir(tmp_path):
    ws = Workspace(str(tmp_path))
    cams = CameraStore(ws.catalog_db)
    for cid in ("nvr-ch1", "nvr-ch2"):
        cams.get_or_create(Camera(id=cid, name=cid))
    cams.close()
    vids = {
        "A": _video("A", "nvr-ch1", W0 + 3600),
        "B": _video("B", "nvr-ch2", W1 - 30, duration=60.0),
        "E": _video("E", "nvr-ch2", W0),
    }
    cat = Catalog(ws.catalog_db)
    for v in vids.values():
        cat.upsert(v)
    cat.close()
    tracks = {
        "e1": _track(vids["E"].id, "car", 0.0),
        "a1": _track(vids["A"].id, "car", 10.0),
        "a2": _track(vids["A"].id, "car", 100.0),
        "a4": _track(vids["A"].id, "car", 110.0, frames=1),   # flicker
        "b1": _track(vids["B"].id, "car", 20.0),
        "b2": _track(vids["B"].id, "car", 40.0),              # past noon
    }
    ts = TrackStore(ws.catalog_db)
    by_vid: dict = {}
    for t in tracks.values():
        by_vid.setdefault(t.video_id, []).append(t)
    for vid_id, ts_list in by_vid.items():
        ts.replace_tracks(vid_id, ts_list)
    ts.close()
    return str(tmp_path), vids, tracks


# --- list_events --------------------------------------------------------------

def test_events_are_exactly_the_tracks_behind_the_count(workdir):
    wd, _, tracks = workdir
    count = count_objects("cars", WINDOW, workdir=wd)
    rows = list_events("cars", WINDOW, workdir=wd)
    assert len(rows) == count.total == 4
    # same tracks the count's evidence manifests, in the same absolute order
    assert [str(r.track_id) for r in rows] == \
        [i.attributes["track_id"] for i in count.evidence]
    assert [r.track_id for r in rows] == [
        tracks["e1"].id, tracks["a1"].id, tracks["a2"].id, tracks["b1"].id]


def test_event_rows_carry_placement_camera_and_frames(workdir):
    wd, vids, tracks = workdir
    rows = list_events("cars", WINDOW, workdir=wd)
    first = rows[0]                       # e1 at exactly W0 on ch2
    assert first.video_id == vids["E"].id
    assert first.category == "car"
    assert first.camera == "nvr-ch2"
    assert first.first_seen_epoch == W0
    assert first.last_seen_epoch == W0 + 5.0
    assert first.frames == 3
    assert rows[3].first_seen_epoch == W1 - 10          # b1


def test_events_limit_caps_from_the_front(workdir):
    wd, _, tracks = workdir
    rows = list_events("cars", WINDOW, workdir=wd, limit=2)
    assert [r.track_id for r in rows] == [tracks["e1"].id, tracks["a1"].id]
    assert list_events("cars", WINDOW, workdir=wd, limit=0) == []


# --- timeline_histogram -------------------------------------------------------

def test_hourly_histogram_hand_derived(workdir):
    """12 one-hour buckets; entity starts at hour offsets 0 (e1), 1 (a1, a2),
    11 (b1) -> [1,2,0,0,0,0,0,0,0,0,0,1]."""
    wd, _, _ = workdir
    buckets = timeline_histogram("cars", WINDOW, workdir=wd, bucket="1h")
    assert len(buckets) == 12
    assert [b.count for b in buckets] == [1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
    assert buckets[0].bucket_start_epoch == W0
    assert buckets[11].bucket_start_epoch == W0 + 11 * 3600
    assert all(b.attributes["width_seconds"] == 3600.0 for b in buckets)


def test_histogram_sums_to_the_count_cross_check(workdir):
    wd, _, _ = workdir
    for bucket in ("1h", "90m", "6h", "1d"):
        total = sum(b.count for b in
                    timeline_histogram("cars", WINDOW, workdir=wd, bucket=bucket))
        assert total == count_objects("cars", WINDOW, workdir=wd).total == 4


def test_partial_last_bucket_is_emitted(workdir):
    """12h window at 5h buckets -> ceil(12/5) = 3 buckets, the last covering
    the final 2h; b1 (hour 11.99..) lands in it."""
    wd, _, _ = workdir
    buckets = timeline_histogram("cars", WINDOW, workdir=wd, bucket="5h")
    assert len(buckets) == 3
    assert [b.count for b in buckets] == [3, 0, 1]


def test_invalid_bucket_rejected(workdir):
    wd, _, _ = workdir
    for bad in ("0h", "h", "1w", "-1h", "1.5h", ""):
        with pytest.raises(ValueError, match="invalid histogram bucket"):
            timeline_histogram("cars", WINDOW, workdir=wd, bucket=bad)


def test_bucket_explosion_guard(workdir):
    """1s buckets over 12h = 43200 buckets > the 10k cap -> loud refusal,
    never an unbounded allocation."""
    wd, _, _ = workdir
    with pytest.raises(ValueError, match="widen the bucket"):
        timeline_histogram("cars", WINDOW, workdir=wd, bucket="1s")


def test_fractional_span_allocates_the_true_ceiling_of_buckets(tmp_path):
    """Regression (review r1, verified failing pre-fix): a 10.5 s window at
    10 s buckets needs ceil(10.5/10) = 2 buckets; the integer-ceiling idiom
    ((span + width - 1) // width) allocated 1, and an entity starting at
    t0 + 10.2 — inside the half-open window — crashed with IndexError."""
    ws = Workspace(str(tmp_path))
    video = Video(source_type=SourceType.local, source_uri="/f", source_key="f",
                  camera_id=None, start_epoch=W0, duration_seconds=60.0,
                  ingest_status=IngestStatus.done)
    cat = Catalog(ws.catalog_db)
    cat.upsert(video)
    cat.close()
    ts = TrackStore(ws.catalog_db)
    ts.replace_tracks(video.id, [_track(video.id, "car", 10.2)])
    ts.close()
    w = TimeWindow(start=datetime(2026, 8, 11, 7, 0, 0),
                   end=datetime(2026, 8, 11, 7, 0, 10, 500000), tz="UTC")
    buckets = timeline_histogram("car", w, workdir=str(tmp_path), bucket="10s")
    assert len(buckets) == 2
    assert [b.count for b in buckets] == [0, 1]


def test_empty_window_yields_no_buckets(workdir):
    wd, _, _ = workdir
    w = TimeWindow(start=datetime(2026, 8, 11, 6, 0), end=datetime(2026, 8, 11, 6, 0),
                   tz="America/Los_Angeles")
    assert timeline_histogram("cars", w, workdir=wd) == []
