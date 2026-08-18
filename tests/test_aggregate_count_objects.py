"""count_objects — the windowed, evidenced count op (typed-query tier, TQ1.e).

Determinism ≠ correctness: the assertions are HAND-DERIVED from the fixture's
epoch worksheet (below), not just stable output. In-window car tracks: e1
(ch2, abs W0 exactly), a1 + a2 (ch1), b1 (ch2, W1-10) = TOTAL 4, ch1=2, ch2=2;
a4 is in-window but single-frame (flicker) so min_frames=2 drops it; b2/b3
past or at the end bound; c1 before the window; d1 on a NULL-epoch video.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from va.contracts.aggregate import TimeWindow
from va.contracts.track import ObjectTrack
from va.contracts.video import Camera, IngestStatus, SourceType, Video
from va.pipeline.aggregate import (
    CAVEAT_NO_REID, CAVEAT_PARKED, CAVEAT_RAW_UPPER_BOUND,
    CAVEAT_START_MEMBERSHIP, count_objects,
)
from va.pipeline.paths import Workspace
from va.storage.structured.cameras import CameraStore
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.tracks import TrackStore

# Same hand-derived worksheet as test_aggregate_select_tracks.py:
#   W0 = Aug-11 2026 00:00 America/Los_Angeles (PDT, UTC-7) = 1786431600
#   W1 = Aug-11 2026 12:00 America/Los_Angeles              = 1786474800
W0 = 1786431600.0
W1 = 1786474800.0

WINDOW = TimeWindow(start=datetime(2026, 8, 11, 0, 0),
                    end=datetime(2026, 8, 11, 12, 0),
                    tz="America/Los_Angeles")


def _video(key, camera_id, start_epoch, duration=120.0, profile=None,
           source_type=SourceType.local):
    return Video(source_type=source_type, source_uri=f"/{key}", source_key=key,
                 camera_id=camera_id, start_epoch=start_epoch,
                 duration_seconds=duration, profile=profile,
                 ingest_status=IngestStatus.done)


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
        "C": _video("C", "nvr-ch1", W0 - 3600),
        "D": _video("D", None, None),
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
        "a3": _track(vids["A"].id, "person", 20.0),
        "a4": _track(vids["A"].id, "car", 110.0, frames=1),   # in-window flicker
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


def test_hand_counted_ground_truth_total_and_per_camera(workdir):
    """HAND-DERIVED truth: 4 in-window cars — ch1 {a1, a2}, ch2 {e1, b1}."""
    wd, _, _ = workdir
    r = count_objects("cars", WINDOW, workdir=wd)
    assert r.total == 4
    assert r.per_camera == {"nvr-ch1": 2, "nvr-ch2": 2}
    assert sum(r.per_camera.values()) == r.total


def test_min_frames_is_an_overridable_named_heuristic(workdir):
    """min_frames=1 admits the flicker track a4 -> 5 total, ch1=3."""
    wd, _, _ = workdir
    r = count_objects("cars", WINDOW, workdir=wd, min_frames=1)
    assert r.total == 5
    assert r.per_camera == {"nvr-ch1": 3, "nvr-ch2": 2}


def test_window_echoed_and_resolution_provenance(workdir):
    wd, _, _ = workdir
    r = count_objects("cars", WINDOW, workdir=wd)
    assert r.window is WINDOW
    assert r.resolution.categories_matched == ["cars", "car"]
    assert r.resolution.category_source == "plural-strip"
    assert r.resolution.dedup_mode == "raw"
    assert r.resolution.dedup_source == "per-window tracks"


def test_standing_caveats_always_present(workdir):
    wd, _, _ = workdir
    r = count_objects("cars", WINDOW, workdir=wd)
    assert CAVEAT_RAW_UPPER_BOUND in r.caveats
    assert CAVEAT_PARKED in r.caveats
    assert CAVEAT_START_MEMBERSHIP in r.caveats
    assert CAVEAT_NO_REID not in r.caveats          # raw was requested
    # single footage domain -> no mixed-workdir caveat
    assert not any("mixed-footage" in c for c in r.caveats)


def test_instance_dedup_falls_back_with_extra_caveat_same_total(workdir):
    wd, _, _ = workdir
    r = count_objects("cars", WINDOW, workdir=wd, dedup="instance")
    assert r.total == 4
    assert CAVEAT_NO_REID in r.caveats
    assert r.resolution.dedup_mode == "raw"         # what actually ran


def test_evidence_is_the_entity_manifest(workdir):
    wd, _, tracks = workdir
    r = count_objects("cars", WINDOW, workdir=wd)
    assert len(r.evidence) == r.total
    got_ids = {i.attributes["track_id"] for i in r.evidence}
    assert got_ids == {str(tracks[k].id) for k in ("e1", "a1", "a2", "b1")}
    first = r.evidence[0]                           # e1: W0 = local midnight
    assert first.modality == "object_count" and first.source_role == 6
    assert first.attributes["camera"] == "nvr-ch2"
    assert "2026-08-11T00:00:00-07:00" in first.content


def test_camera_filter_composes(workdir):
    wd, _, _ = workdir
    r = count_objects("cars", WINDOW, workdir=wd, cameras=["nvr-ch2"])
    assert r.total == 2
    assert r.per_camera == {"nvr-ch2": 2}


def test_zero_match_is_an_honest_zero_not_a_crash(workdir):
    """'vehicles' matches no detector class under the plural-strip stub —
    total 0 WITH full provenance/caveats (Role-12 territory, plan §5.1)."""
    wd, _, _ = workdir
    r = count_objects("vehicles", WINDOW, workdir=wd)
    assert r.total == 0 and r.per_camera == {} and r.evidence == []
    assert r.resolution.categories_matched == ["vehicles", "vehicle"]
    assert len(r.caveats) >= 3


def test_aev_only_workdir_is_not_applicable_never_a_bare_zero(tmp_path):
    """Batch-review major (2026-08-17): a pure A-EV workdir (tracks present,
    start_epoch NULL everywhere) must NOT ship a confident bare 0 — the
    leading caveat says NOT APPLICABLE and the excluded matched tracks are
    counted and named."""
    ws = Workspace(str(tmp_path))
    video = _video("aev", None, None)            # start_epoch NULL, done
    cat = Catalog(ws.catalog_db)
    cat.upsert(video)
    cat.close()
    ts = TrackStore(ws.catalog_db)
    ts.replace_tracks(video.id, [_track(video.id, "car", 10.0),
                                 _track(video.id, "car", 30.0)])
    ts.close()

    r = count_objects("cars", WINDOW, workdir=str(tmp_path))
    assert r.total == 0
    assert "NOT APPLICABLE" in r.caveats[0]
    assert "start_epoch" in r.caveats[0]
    assert any("2 matched 'cars' track(s)" in c and "EXCLUDED" in c
               for c in r.caveats)
    assert r.attributes["window_anchoring"] == {
        "placed_videos": 0, "unplaced_matching_tracks": 2}


def test_unplaced_matched_tracks_are_disclosed_alongside_a_real_count(workdir):
    """The fixture's video D holds 1 in-corpus car track with no wall-clock
    anchor: the count stays 4 but the exclusion is named."""
    wd, _, _ = workdir
    r = count_objects("cars", WINDOW, workdir=wd)
    assert r.total == 4
    assert any("1 matched 'cars' track(s)" in c and "EXCLUDED" in c
               for c in r.caveats)
    assert r.attributes["window_anchoring"]["placed_videos"] > 0
    assert r.attributes["window_anchoring"]["unplaced_matching_tracks"] == 1


def test_mixed_footage_workdir_gets_the_caveat(workdir):
    """Adding a DONE video from another footage domain (security/nvr) trips
    the mixed-workdir disclosure (reuses Catalog.footage_domains, done-only)."""
    wd, _, _ = workdir
    cat = Catalog(Workspace(wd).catalog_db)
    cat.upsert(_video("N", "nvr-ch1", W0 + 7200, profile="security",
                      source_type=SourceType.nvr_recorded))
    cat.close()
    r = count_objects("cars", WINDOW, workdir=wd)
    assert any("mixed-footage workdir" in c for c in r.caveats)
    assert r.total == 4                              # the count itself is unchanged
