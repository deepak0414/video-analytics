"""Tracker geometry pass-through invariant (WS4.d review round-1 major).

The crop-to-track join in ingest keys on EXACT detection geometry, so every
tracker adapter must return the ORIGINAL detections (model_copy with ids set),
never boxes reconstructed from the tracker's internal representation — the
bytetrack adapter's old float32(x1000) round-trip perturbed ~1-4% of keys and
silently dropped those tracks' appearance refs.
"""
from uuid import uuid4

import pytest

from va.contracts.detection import Detection


def _frames():
    # awkward float fractions that do NOT survive a float32 round-trip
    def det(x, conf):
        return Detection(object_class="person", confidence=conf,
                         bbox_x=x, bbox_y=0.1234567, bbox_w=0.2000001,
                         bbox_h=0.3333333)
    return [
        (0.0, [det(0.1000003, 0.9), det(0.7000007, 0.8)]),
        (1.0, [det(0.1100003, 0.85), det(0.7100007, 0.75)]),
        (2.0, [det(0.1200003, 0.95)]),
    ]


def _assert_passthrough(result, frames):
    originals = {(ts, d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h)
                 for ts, dets in frames for d in dets}
    assert result.detections, "tracker returned no detections"
    for d in result.detections:
        key = (d.timestamp, d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h)
        assert key in originals, f"geometry not bit-identical to an input: {key}"
        assert d.track_id is not None


def test_iou_tracker_passes_geometry_through():
    from va.adapters.object_tracker.iou_inproc import IouTracker

    frames = _frames()
    _assert_passthrough(IouTracker().track(uuid4(), frames), frames)


def test_bytetrack_passes_geometry_through():
    pytest.importorskip("supervision")
    from va.adapters.object_tracker.bytetrack_inproc import ByteTrackTracker

    frames = _frames()
    _assert_passthrough(ByteTrackTracker().track(uuid4(), frames), frames)
