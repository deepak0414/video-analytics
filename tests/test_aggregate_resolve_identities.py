"""resolve_identities seam (typed-query tier, TQ1.d).

Raw mode must reproduce today's `TrackStore.distinct_counts` semantics
(one track = one entity, `min_frames` flicker filter); instance mode is
accepted but falls back to raw with an explicit no-ReID caveat and honest
provenance (`dedup_mode` reports what RAN, never what was requested).
"""
from __future__ import annotations

from collections import Counter
from uuid import uuid4

import pytest

from va.contracts.track import ObjectTrack
from va.contracts.video import SourceType, Video
from va.pipeline.aggregate import (
    CAVEAT_NO_REID, DEDUP_MODE_RAW, DEDUP_SOURCE_PER_WINDOW_TRACKS,
    resolve_identities,
)
from va.pipeline.paths import Workspace
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.tracks import PlacedTrack, TrackStore

BASE = 1_786_431_600.0  # arbitrary epoch anchor (Aug-11 2026 00:00 PDT)


def _placed(cls, first_seen=10.0, frames=3, camera="nvr-ch1"):
    t = ObjectTrack(id=uuid4(), video_id=uuid4(), object_class=cls,
                    track_confidence=0.9, first_seen=first_seen,
                    last_seen=first_seen + 5.0, frame_count=frames)
    return PlacedTrack(track=t, camera=camera,
                       first_seen_epoch=BASE + first_seen,
                       last_seen_epoch=BASE + first_seen + 5.0)


def test_raw_one_track_one_entity_with_flicker_filter():
    """Hand-derived: 3 car tracks (one single-frame flicker) + 1 person ->
    min_frames=2 keeps 2 cars + 1 person = 3 entities."""
    tracks = [_placed("car"), _placed("car", 20.0),
              _placed("car", 30.0, frames=1),          # flicker: dropped
              _placed("person", 40.0)]
    res = resolve_identities(tracks, mode="raw", min_frames=2)
    assert len(res.entities) == 3
    assert Counter(e.category for e in res.entities) == {"car": 2, "person": 1}
    assert res.dedup_mode == DEDUP_MODE_RAW
    assert res.dedup_source == DEDUP_SOURCE_PER_WINDOW_TRACKS
    assert res.caveats == []


def test_entities_carry_placement_and_their_track():
    p = _placed("car", 15.0, camera="nvr-ch2")
    e = resolve_identities([p]).entities[0]
    assert e.camera == "nvr-ch2"
    assert e.first_seen_epoch == BASE + 15.0
    assert e.last_seen_epoch == BASE + 20.0
    assert e.tracks == (p,)


def test_instance_mode_falls_back_to_raw_with_caveat():
    tracks = [_placed("car"), _placed("car", 20.0)]
    raw = resolve_identities(tracks, mode="raw")
    inst = resolve_identities(tracks, mode="instance")
    # same entities, honest provenance: what RAN was raw
    assert [e.tracks for e in inst.entities] == [e.tracks for e in raw.entities]
    assert inst.dedup_mode == DEDUP_MODE_RAW
    assert inst.dedup_source == DEDUP_SOURCE_PER_WINDOW_TRACKS
    assert inst.caveats == [CAVEAT_NO_REID]
    assert "not yet available" in CAVEAT_NO_REID


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown dedup mode"):
        resolve_identities([_placed("car")], mode="reid")


def test_min_frames_one_keeps_flicker():
    tracks = [_placed("car", frames=1)]
    assert len(resolve_identities(tracks, min_frames=1).entities) == 1
    assert len(resolve_identities(tracks, min_frames=2).entities) == 0


def test_raw_mode_reproduces_distinct_counts_on_a_db_fixture(tmp_path):
    """Parity with today's `distinct_counts` on the same stored tracks.

    Hand-derived: cars with frame_counts 3,2,1 and persons 4,2 at min_frames=2
    -> car=2, person=2 from BOTH paths."""
    ws = Workspace(str(tmp_path))
    video = Video(source_type=SourceType.local, source_uri="/v", source_key="v",
                  start_epoch=BASE, duration_seconds=600.0)
    cat = Catalog(ws.catalog_db)
    cat.upsert(video)
    cat.close()

    def _t(cls, first, frames):
        return ObjectTrack(id=uuid4(), video_id=video.id, object_class=cls,
                           track_confidence=0.9, first_seen=first,
                           last_seen=first + 5.0, frame_count=frames)

    stored = [_t("car", 10.0, 3), _t("car", 20.0, 2), _t("car", 30.0, 1),
              _t("person", 40.0, 4), _t("person", 50.0, 2)]
    store = TrackStore(ws.catalog_db)
    try:
        store.replace_tracks(video.id, stored)
        legacy = {c.object_class: c.distinct
                  for c in store.distinct_counts(["car", "person"], min_frames=2)}
        placed = store.select_placed(["car", "person"], BASE, BASE + 600.0)
        res = resolve_identities(placed, mode="raw", min_frames=2)
    finally:
        store.close()
    seam = Counter(e.category for e in res.entities)
    assert legacy == {"car": 2, "person": 2}
    assert dict(seam) == legacy
