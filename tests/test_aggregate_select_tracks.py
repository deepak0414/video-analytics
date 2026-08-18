"""select_tracks — windowed, tz-correct track selection (typed-query tier, TQ1.c).

Ground truth is HAND-DERIVED (see the epoch worksheet below): a synthetic
catalog with tracks at known absolute placements, and the exact in-window set
asserted. Includes the regression pin for the silent-0 bug this tier exists to
prevent: epoch bounds built as SQLite `strftime('%s',...)` TEXT compare above
every number and match nothing.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from va.contracts.aggregate import TimeWindow
from va.contracts.track import ObjectTrack
from va.contracts.video import Camera, SourceType, Video
from va.pipeline.aggregate import select_tracks
from va.pipeline.paths import Workspace
from va.storage.structured.cameras import CameraStore
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.schema import connect
from va.storage.structured.tracks import TrackStore

# Epoch worksheet (hand-derived; independently: 2026-01-01T00:00Z = 1767225600,
# +222 days = 2026-08-11T00:00Z = 1786406400; PDT = UTC-7):
#   W0 = Aug-11 00:00 America/Los_Angeles = 07:00Z = 1786431600
#   W1 = Aug-11 12:00 America/Los_Angeles = 19:00Z = 1786474800
W0 = 1786431600.0
W1 = 1786474800.0

WINDOW = TimeWindow(start=datetime(2026, 8, 11, 0, 0),
                    end=datetime(2026, 8, 11, 12, 0),
                    tz="America/Los_Angeles")


def _video(key, camera_id, start_epoch, duration=120.0):
    return Video(source_type=SourceType.local, source_uri=f"/{key}", source_key=key,
                 camera_id=camera_id, start_epoch=start_epoch,
                 duration_seconds=duration)


def _track(video_id, cls, first_seen, last_seen=None, frames=3):
    return ObjectTrack(id=uuid4(), video_id=video_id, object_class=cls,
                       track_confidence=0.9, first_seen=first_seen,
                       last_seen=last_seen if last_seen is not None else first_seen + 5.0,
                       frame_count=frames)


@pytest.fixture()
def workdir(tmp_path):
    """Synthetic catalog. Hand-derived in-window truth for class 'car':

    video E (ch2, epoch W0):      e1 car @0    -> abs W0        IN (start-inclusive)
    video A (ch1, epoch W0+3600): a1 car @10   -> abs W0+3610   IN
                                  a2 car @100  -> abs W0+3700   IN
                                  a3 person @20                 (class-filtered)
    video B (ch2, epoch W1-30):   b1 car @20   -> abs W1-10     IN
                                  b2 car @40   -> abs W1+10     OUT (past noon)
                                  b3 car @30   -> abs W1 exact  OUT (half-open end)
    video C (ch1, epoch W0-3600): c1 car @50   -> abs W0-3550   OUT (before window)
    video D (no epoch, A-EV):     d1 car @5                     SKIPPED (NULL epoch)

    => 4 in-window car tracks, ordered e1, a1, a2, b1; per camera ch1=2, ch2=2.
    """
    ws = Workspace(str(tmp_path))
    cams = CameraStore(ws.catalog_db)
    for cid in ("nvr-ch1", "nvr-ch2"):
        cams.get_or_create(Camera(id=cid, name=cid))
    cams.close()

    cat = Catalog(ws.catalog_db)
    vids = {
        "A": _video("A", "nvr-ch1", W0 + 3600),
        "B": _video("B", "nvr-ch2", W1 - 30, duration=60.0),
        "C": _video("C", "nvr-ch1", W0 - 3600),
        "D": _video("D", None, None),
        "E": _video("E", "nvr-ch2", W0),
    }
    for v in vids.values():
        cat.upsert(v)
    cat.close()

    tracks = {
        "e1": _track(vids["E"].id, "car", 0.0),
        "a1": _track(vids["A"].id, "car", 10.0),
        "a2": _track(vids["A"].id, "car", 100.0),
        "a3": _track(vids["A"].id, "person", 20.0),
        "b1": _track(vids["B"].id, "car", 20.0),
        "b2": _track(vids["B"].id, "car", 40.0),
        "b3": _track(vids["B"].id, "car", 30.0),
        "c1": _track(vids["C"].id, "car", 50.0),
        "d1": _track(vids["D"].id, "car", 5.0),
    }
    ts = TrackStore(ws.catalog_db)
    by_vid: dict = {}
    for t in tracks.values():
        by_vid.setdefault(t.video_id, []).append(t)
    for vid_id, ts_list in by_vid.items():
        ts.replace_tracks(vid_id, ts_list)
    ts.close()
    return str(tmp_path), vids, tracks


def test_exactly_the_in_window_tracks_in_absolute_order(workdir):
    wd, vids, tracks = workdir
    got = select_tracks(["car"], WINDOW, workdir=wd)
    assert [p.track.id for p in got] == [
        tracks["e1"].id, tracks["a1"].id, tracks["a2"].id, tracks["b1"].id]
    # placements carry through: e1 at exactly W0 (07:00 UTC = local midnight —
    # the tz-conversion case), b1 ten seconds before local noon.
    assert got[0].first_seen_epoch == W0
    assert got[3].first_seen_epoch == W1 - 10
    assert got[0].camera == "nvr-ch2" and got[1].camera == "nvr-ch1"


def test_boundary_semantics_start_inclusive_end_exclusive(workdir):
    wd, _, tracks = workdir
    ids = {p.track.id for p in select_tracks(["car"], WINDOW, workdir=wd)}
    assert tracks["e1"].id in ids       # abs == window start -> in
    assert tracks["b3"].id not in ids   # abs == window end   -> out (half-open)


def test_camera_filter(workdir):
    wd, _, tracks = workdir
    got = select_tracks(["car"], WINDOW, workdir=wd, cameras=["nvr-ch1"])
    assert [p.track.id for p in got] == [tracks["a1"].id, tracks["a2"].id]


def test_null_epoch_videos_are_skipped(workdir):
    wd, _, tracks = workdir
    ids = {p.track.id for p in select_tracks(["car"], WINDOW, workdir=wd)}
    assert tracks["d1"].id not in ids


def test_tz_changes_the_answer(workdir):
    """Same naive wall-clock numbers, tz=UTC instead of local, and the SET
    changes, not just the size: b1 (abs 18:59:50Z) drops out of 00:00-12:00
    UTC, while c1 (Aug-10 23:00:50 local = Aug-11 06:00:50Z) comes IN. Pins
    that tz is load-bearing, not decoration."""
    wd, _, tracks = workdir
    utc_window = TimeWindow(start=datetime(2026, 8, 11, 0, 0),
                            end=datetime(2026, 8, 11, 12, 0), tz="UTC")
    got = {p.track.id for p in select_tracks(["car"], utc_window, workdir=wd)}
    assert got == {tracks["e1"].id, tracks["a1"].id, tracks["a2"].id,
                   tracks["c1"].id}


def test_empty_categories_returns_empty(workdir):
    wd, _, _ = workdir
    assert select_tracks([], WINDOW, workdir=wd) == []


def test_empty_camera_subset_means_no_rows_not_all_cameras(workdir):
    """cameras=[] is an explicit EMPTY selection -> 0 rows (a falsy guard that
    treated it as 'all cameras' would silently inflate a filtered count to the
    unfiltered total). None keeps meaning 'no restriction'."""
    wd, _, _ = workdir
    assert select_tracks(["car"], WINDOW, workdir=wd, cameras=[]) == []
    assert len(select_tracks(["car"], WINDOW, workdir=wd, cameras=None)) == 4


# --- the silent-0 regression pin ---------------------------------------------

def test_strftime_text_bound_is_the_false_zero_bug(workdir):
    """THE bug this tier exists to prevent, demonstrated on this very fixture:
    a strftime('%s',...) bound is TEXT, SQLite orders every number below any
    text, so the >= comparison silently matches NOTHING (0 rows) where the
    numeric truth is 4. If select_placed were ever rewritten with strftime
    bounds, test_exactly_the_in_window_tracks_in_absolute_order would report
    the same false 0 and fail."""
    wd, _, _ = workdir
    conn = connect(Workspace(wd).catalog_db)
    try:
        # Sanity: numeric bounds find the 4 in-window car tracks.
        numeric = conn.execute(
            "SELECT COUNT(*) FROM object_tracks t JOIN videos v ON v.id = t.video_id "
            "WHERE v.start_epoch IS NOT NULL AND lower(t.object_class) = 'car' "
            "AND (v.start_epoch + t.first_seen) >= ? "
            "AND (v.start_epoch + t.first_seen) < ?", (W0, W1)).fetchone()[0]
        assert numeric == 4
        # The broken form: identical SQL, bounds via strftime -> TEXT -> 0 rows.
        broken = conn.execute(
            "SELECT COUNT(*) FROM object_tracks t JOIN videos v ON v.id = t.video_id "
            "WHERE v.start_epoch IS NOT NULL AND lower(t.object_class) = 'car' "
            "AND (v.start_epoch + t.first_seen) >= strftime('%s', '2026-08-11 07:00:00') "
            "AND (v.start_epoch + t.first_seen) < strftime('%s', '2026-08-11 19:00:00')",
        ).fetchone()[0]
        assert broken == 0
    finally:
        conn.close()


def test_text_epoch_bounds_raise_loudly(workdir):
    """The store refuses TEXT bounds outright — the strftime failure mode can
    never be silent through this API."""
    wd, _, _ = workdir
    store = TrackStore(Workspace(wd).catalog_db)
    try:
        with pytest.raises(TypeError, match="NUMBER of UTC epoch seconds"):
            store.select_placed(["car"], "1786431600", W1)
        with pytest.raises(TypeError, match="NUMBER of UTC epoch seconds"):
            store.select_placed(["car"], W0, "1786474800")
    finally:
        store.close()
