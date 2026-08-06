"""WS4.b — motion-episode Role-1 backend: MotionSource episodes become segments.

Unit tests drive MotionEpisodeSceneDetector directly against a fake MotionSource
with KNOWN windows (the ground truth is literal in each test, per the loop
file's done-when); the integration test proves the whole path — profile selects
the backend, sidecar supplies epoch events, ingest lands exactly those windows
in the segments table.
"""
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml

from va.adapters.scene_detector.motion_episodes_inproc import MotionEpisodeSceneDetector
from va.contracts.motion import MotionEvent
from va.contracts.video import Camera
from va.roles.scene_detector import SceneContext

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"
T0 = 1_700_000_000.0  # arbitrary fixed epoch; tests are relative to it


class FakeMotionSource:
    """Returns canned events verbatim; records what it was asked."""

    def __init__(self, events=(), raise_exc=None):
        self._events = list(events)
        self._raise = raise_exc
        self.calls = []

    def events(self, start_epoch, end_epoch, camera_ref=None):
        self.calls.append((start_epoch, end_epoch, camera_ref))
        if self._raise is not None:
            raise self._raise
        return list(self._events)


def _ev(start, end, cam="1"):
    return MotionEvent(camera_ref=cam, start_epoch=T0 + start, end_epoch=T0 + end)


def _ctx(duration=60.0, camera_ref="1"):
    return SceneContext(start_epoch=T0, camera_ref=camera_ref,
                        duration_seconds=duration)


# --- unit: episodes -> spans -------------------------------------------------

def test_known_windows_become_exactly_those_segments():
    # gap_s=10 keeps the two episodes distinct (their 30 s gap would be merged
    # by the default gap_s=30 — clustering is inclusive at the boundary).
    src = FakeMotionSource([_ev(5, 10), _ev(40, 50)])
    det = MotionEpisodeSceneDetector(src, pad_s=2.0, gap_s=10.0)
    assert det.detect("unused.mp4", _ctx(60.0)) == [(3.0, 12.0), (38.0, 52.0)]


def test_range_and_camera_are_forwarded_to_the_source():
    src = FakeMotionSource([])
    MotionEpisodeSceneDetector(src).detect("unused.mp4", _ctx(60.0, camera_ref="7"))
    assert src.calls == [(T0, T0 + 60.0, "7")]


def test_event_straddling_chunk_start_is_clamped_to_zero():
    src = FakeMotionSource([_ev(-5, 5)])
    det = MotionEpisodeSceneDetector(src, pad_s=2.0)
    assert det.detect("unused.mp4", _ctx(60.0)) == [(0.0, 7.0)]


def test_event_beyond_chunk_end_is_dropped():
    # Entirely after the chunk: clamps to a zero/negative span -> dropped.
    src = FakeMotionSource([_ev(70, 80)])
    det = MotionEpisodeSceneDetector(src, pad_s=2.0)
    assert det.detect("unused.mp4", _ctx(60.0)) == []


def test_padding_merges_overlapping_spans():
    # gap_s=1 keeps the episodes apart in clustering; pad_s=2 makes the padded
    # spans overlap ((3,12) and (10,17)) -> one merged segment.
    src = FakeMotionSource([_ev(5, 10), _ev(12, 15)])
    det = MotionEpisodeSceneDetector(src, pad_s=2.0, gap_s=1.0)
    assert det.detect("unused.mp4", _ctx(60.0)) == [(3.0, 17.0)]


def test_sliver_below_min_span_is_dropped():
    src = FakeMotionSource([_ev(59.9, 65)])
    det = MotionEpisodeSceneDetector(src, pad_s=0.0, min_span_s=0.5)
    assert det.detect("unused.mp4", _ctx(60.0)) == []


def test_quiet_chunk_with_epoch_yields_no_segments():
    det = MotionEpisodeSceneDetector(FakeMotionSource([]))
    assert det.detect("unused.mp4", _ctx(60.0)) == []


# --- unit: degraded modes (both warn, both full-span) ------------------------

def test_missing_start_epoch_degrades_to_full_span(caplog):
    det = MotionEpisodeSceneDetector(FakeMotionSource([_ev(5, 10)]))
    ctx = SceneContext(start_epoch=None, duration_seconds=42.0)
    with caplog.at_level("WARNING"):
        assert det.detect("unused.mp4", ctx) == [(0.0, 42.0)]
    assert any("no start_epoch" in r.message for r in caplog.records)


def test_motion_source_failure_degrades_to_full_span(caplog):
    det = MotionEpisodeSceneDetector(FakeMotionSource(raise_exc=OSError("nvr down")))
    with caplog.at_level("WARNING"):
        assert det.detect("unused.mp4", _ctx(30.0)) == [(0.0, 30.0)]
    assert any("MotionSource failed" in r.message for r in caplog.records)


def test_motion_source_config_is_part_of_scene_provenance(tmp_path, monkeypatch):
    # Motion-episode segments are a function of where motion windows come from
    # — switching/reconfiguring motion_source must change the scene_detector
    # fingerprint (else a missed stale, §6-b).
    from va.configuration import load_config
    from va.provenance import role_fingerprint

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))
    base = role_fingerprint(
        "scene_detector", load_config(footage_profile="security"))

    doc = yaml.safe_load((cdir / "roles.yaml").read_text())
    doc["roles"]["motion_source"] = {"backend": "inproc", "model": "lnr-eventlog"}
    (cdir / "roles.yaml").write_text(yaml.safe_dump(doc))
    switched = role_fingerprint(
        "scene_detector", load_config(footage_profile="security"))
    assert switched["fingerprint"] != base["fingerprint"]

    # a purely visual scene model must NOT depend on motion_source
    hist_base = role_fingerprint("scene_detector", load_config(cdir))
    doc["roles"]["motion_source"]["model"] = "sidecar"
    (cdir / "roles.yaml").write_text(yaml.safe_dump(doc))
    assert role_fingerprint(
        "scene_detector", load_config(cdir))["fingerprint"] == hist_base["fingerprint"]


# --- registry selection ------------------------------------------------------

def test_security_profile_selects_motion_episodes_backend():
    from va.configuration import load_config
    from va.registry import get_scene_detector

    det = get_scene_detector(load_config(REPO_CONFIG, footage_profile="security"))
    assert isinstance(det, MotionEpisodeSceneDetector)
    # generic stays on the visual default
    from va.adapters.scene_detector.histogram_inproc import HistogramSceneDetector
    assert isinstance(get_scene_detector(load_config(REPO_CONFIG)),
                      HistogramSceneDetector)


def test_spec_knobs_reach_the_backend(tmp_path, monkeypatch):
    from va.configuration import load_config
    from va.registry import get_scene_detector

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "knobs.yaml").write_text(yaml.safe_dump({
        "roles": {"scene_detector": {
            "model": "motion-episodes", "pad_s": 5.0, "gap_s": 7.0,
            "min_span_s": 1.5,
        }}
    }))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))
    det = get_scene_detector(load_config(footage_profile="knobs"))
    assert (det.pad_s, det.gap_s, det.min_span_s) == (5.0, 7.0, 1.5)


# --- integration: the done-when oracle ---------------------------------------

def test_ingest_lands_exactly_the_known_motion_windows(tmp_path, monkeypatch):
    """Profile -> motion backend -> sidecar events -> segments table, end to end.

    Ground truth: ONE event on this camera inside the 6 s chunk (2..4 s, padded
    by 1 -> 1..5). A second event beyond the chunk and a third on another
    camera must not produce segments.
    """
    from va.media.synth import write_color_video
    from va.pipeline.ingest import ingest
    from va.pipeline.paths import Workspace
    from va.sources.base import resolve_source
    from va.storage.structured.cameras import CameraStore
    from va.storage.structured.catalog_sqlite import Catalog

    events_file = tmp_path / "motion.json"
    events_file.write_text(json.dumps({"events": [
        {"camera_ref": "1", "start_epoch": T0 + 2, "end_epoch": T0 + 4},
        {"camera_ref": "1", "start_epoch": T0 + 70, "end_epoch": T0 + 80},
        {"camera_ref": "2", "start_epoch": T0 + 1, "end_epoch": T0 + 5},
    ]}))
    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "motionprof.yaml").write_text(yaml.safe_dump({
        "roles": {
            "scene_detector": {"model": "motion-episodes", "pad_s": 1.0},
            "motion_source": {"backend": "inproc", "model": "sidecar",
                              "events_file": str(events_file)},
        }
    }))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    clip = write_color_video(tmp_path / "clip.mp4",
                             [("red", (220, 30, 30), 6.0)], fps=10)

    # Attach chunk metadata to a pending row first — the A-MCLSSRVF pull path's
    # job (WS4.c); until then this is how a chunk gets its wall-clock placement.
    ws = tmp_path / ".va"
    ws.mkdir()
    resolved = resolve_source(str(clip)).resolve(str(clip))
    catalog = Catalog(Workspace(str(ws)).catalog_db)
    video, created = catalog.get_or_create(resolved)
    assert created
    cams = CameraStore(Workspace(str(ws)).catalog_db)
    cams.get_or_create(Camera(id="cam-a", name="driveway", source_ref="1"))
    cams.close()
    catalog.set_camera(video.id, "cam-a")
    catalog.set_start_epoch(video.id, T0)
    catalog.close()

    result = ingest(str(clip), workdir=str(ws), fps=1.0, profile="motionprof")
    assert result.segments == 1

    conn = sqlite3.connect(ws / "catalog.db")
    rows = conn.execute(
        "SELECT start_time, end_time FROM segments WHERE video_id = ? "
        "ORDER BY segment_index", (str(video.id),)
    ).fetchall()
    conn.close()
    assert rows == [(1.0, 5.0)]


def test_reingest_keeps_motion_segmentation(tmp_path, monkeypatch):
    """Round-1 review major: reingest used to reattach camera/start_epoch only
    AFTER ingest(), so Role 1 ran epoch-blind and an epoch-placed chunk
    silently degraded to one full-span segment (stamped provenance-current).
    The metadata must be on the recreated row BEFORE ingest runs."""
    from va.media.synth import write_color_video
    from va.pipeline.ingest import ingest
    from va.pipeline.manage import reingest_video
    from va.pipeline.paths import Workspace
    from va.sources.base import resolve_source
    from va.storage.structured.cameras import CameraStore
    from va.storage.structured.catalog_sqlite import Catalog

    events_file = tmp_path / "motion.json"
    events_file.write_text(json.dumps({"events": [
        {"camera_ref": "1", "start_epoch": T0 + 2, "end_epoch": T0 + 4},
    ]}))
    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "motionprof.yaml").write_text(yaml.safe_dump({
        "roles": {
            "scene_detector": {"model": "motion-episodes", "pad_s": 1.0},
            "motion_source": {"backend": "inproc", "model": "sidecar",
                              "events_file": str(events_file)},
        }
    }))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    clip = write_color_video(tmp_path / "clip.mp4",
                             [("red", (220, 30, 30), 6.0)], fps=10)
    ws = tmp_path / ".va"
    ws.mkdir()
    resolved = resolve_source(str(clip)).resolve(str(clip))
    catalog = Catalog(Workspace(str(ws)).catalog_db)
    video, _ = catalog.get_or_create(resolved)
    cams = CameraStore(Workspace(str(ws)).catalog_db)
    cams.get_or_create(Camera(id="cam-a", name="driveway", source_ref="1"))
    cams.close()
    catalog.set_camera(video.id, "cam-a")
    catalog.set_start_epoch(video.id, T0)
    catalog.close()
    ingest(str(clip), workdir=str(ws), fps=1.0, profile="motionprof")

    result = reingest_video(str(ws), str(clip), fps=1.0, profile="motionprof")
    assert result is not None and result.segments == 1

    conn = sqlite3.connect(ws / "catalog.db")
    rows = conn.execute(
        "SELECT start_time, end_time FROM segments ORDER BY segment_index"
    ).fetchall()
    conn.close()
    # identical ground truth to the first ingest — NOT a (0.0, 6.0) full span
    assert rows == [(1.0, 5.0)]


def test_dangling_camera_id_warns_and_still_ingests(tmp_path, monkeypatch, caplog):
    """Round-2 review minor: camera_id pointing at no cameras row (FK is
    unenforced until WS4.c) must warn — an unfiltered motion query is a
    degraded mode like the others, not a silent behavior."""
    from va.media.synth import write_color_video
    from va.pipeline.ingest import ingest
    from va.pipeline.paths import Workspace
    from va.sources.base import resolve_source
    from va.storage.structured.catalog_sqlite import Catalog

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "motionprof.yaml").write_text(yaml.safe_dump({
        "roles": {"scene_detector": {"model": "motion-episodes"}}
    }))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    clip = write_color_video(tmp_path / "clip.mp4",
                             [("red", (220, 30, 30), 3.0)], fps=10)
    ws = tmp_path / ".va"
    ws.mkdir()
    resolved = resolve_source(str(clip)).resolve(str(clip))
    catalog = Catalog(Workspace(str(ws)).catalog_db)
    video, _ = catalog.get_or_create(resolved)
    catalog.set_camera(video.id, "no-such-camera")   # dangling: no cameras row
    catalog.set_start_epoch(video.id, T0)
    catalog.close()

    with caplog.at_level("WARNING"):
        result = ingest(str(clip), workdir=str(ws), fps=1.0, profile="motionprof")
    assert result.video.ingest_status.value == "done"
    assert any("missing camera" in r.message for r in caplog.records)


def test_ingest_without_epoch_degrades_to_full_span(tmp_path, monkeypatch):
    """No start_epoch on the row -> one full-span segment (documented fallback)."""
    from va.media.synth import write_color_video
    from va.pipeline.ingest import ingest

    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    (cdir / "profiles" / "footage" / "motionprof.yaml").write_text(yaml.safe_dump({
        "roles": {"scene_detector": {"model": "motion-episodes"}}
    }))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    clip = write_color_video(tmp_path / "clip.mp4",
                             [("red", (220, 30, 30), 3.0)], fps=10)
    result = ingest(str(clip), workdir=str(tmp_path / ".va"), fps=1.0,
                    profile="motionprof")
    assert result.segments == 1

    conn = sqlite3.connect(tmp_path / ".va" / "catalog.db")
    ((start, end),) = conn.execute(
        "SELECT start_time, end_time FROM segments").fetchall()
    conn.close()
    assert start == 0.0
    assert end == pytest.approx(3.0, abs=0.5)
