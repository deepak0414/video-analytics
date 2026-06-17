from uuid import uuid4

from va.adapters.object_tracker.iou_inproc import IouTracker
from va.contracts.detection import Detection
from va.roles.object_tracker import ObjectTracker
from va.storage.structured.tracks import TrackStore


def _det(cls, x, y, conf=0.9, w=0.3, h=0.3):
    return Detection(object_class=cls, confidence=conf,
                     bbox_x=x, bbox_y=y, bbox_w=w, bbox_h=h)


def test_two_objects_become_two_tracks_despite_drift():
    vid = uuid4()
    tracker = IouTracker()
    assert isinstance(tracker, ObjectTracker)
    # red box drifts right slowly; blue box static — 4 frames @1s apart
    frames = [
        (float(t), [_det("red", 0.10 + 0.05 * t, 0.1), _det("blue", 0.6, 0.6)])
        for t in range(4)
    ]
    result = tracker.track(vid, frames)

    assert len(result.tracks) == 2
    by_class = {t.object_class: t for t in result.tracks}
    assert by_class["red"].frame_count == 4
    assert by_class["blue"].frame_count == 4
    assert by_class["red"].first_seen == 0.0 and by_class["red"].last_seen == 3.0

    # every detection got a track_id, consistent per object
    red_ids = {d.track_id for d in result.detections if d.object_class == "red"}
    assert len(red_ids) == 1 and None not in red_ids


def test_gap_beyond_max_creates_new_track():
    vid = uuid4()
    tracker = IouTracker(max_gap_seconds=2.5)
    # same place, but absent for 5s -> two distinct tracks
    frames = [(0.0, [_det("red", 0.1, 0.1)]), (5.0, [_det("red", 0.1, 0.1)])]
    result = tracker.track(vid, frames)
    assert len(result.tracks) == 2


def test_same_class_far_apart_are_distinct():
    vid = uuid4()
    result = IouTracker().track(vid, [
        (0.0, [_det("red", 0.05, 0.05), _det("red", 0.65, 0.65)]),
        (1.0, [_det("red", 0.05, 0.05), _det("red", 0.65, 0.65)]),
    ])
    assert len(result.tracks) == 2          # two simultaneous reds = two objects
    assert all(t.frame_count == 2 for t in result.tracks)


def test_track_store_distinct_counts(tmp_path):
    vid = uuid4()
    tracker = IouTracker()
    result = tracker.track(vid, [
        (0.0, [_det("red", 0.05, 0.05), _det("red", 0.65, 0.65), _det("blue", 0.4, 0.4)]),
        (1.0, [_det("red", 0.05, 0.05), _det("red", 0.65, 0.65), _det("blue", 0.4, 0.4)]),
    ])
    store = TrackStore(tmp_path / "catalog.db")
    store.replace_tracks(vid, result.tracks)
    assert store.count(vid) == 3

    counts = {c.object_class: c.distinct for c in store.distinct_counts(["red", "blue"])}
    assert counts == {"red": 2, "blue": 1}   # the distinct-counting payoff

    # idempotent replace
    store.replace_tracks(vid, result.tracks[:1])
    assert store.count(vid) == 1
