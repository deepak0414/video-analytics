"""`va aggregate` CLI (typed-query tier, TQ1.g).

Runs the real argparse path (`cli.main(argv)`) against the same hand-derived
fixture as the op tests: 4 in-window cars — ch1 {a1, a2}, ch2 {e1, b1}.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from va.cli import main
from va.contracts.track import ObjectTrack
from va.contracts.video import Camera, IngestStatus, SourceType, Video
from va.pipeline.paths import Workspace
from va.storage.structured.cameras import CameraStore
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.tracks import TrackStore

# W0 = Aug-11 2026 00:00 PDT = 1786431600; W1 = 12:00 PDT = 1786474800.
W0 = 1786431600.0
W1 = 1786474800.0


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
    tracks = [
        _track(vids["E"].id, "car", 0.0),
        _track(vids["A"].id, "car", 10.0),
        _track(vids["A"].id, "car", 100.0),
        _track(vids["B"].id, "car", 20.0),
        _track(vids["B"].id, "car", 40.0),      # past noon -> out
    ]
    ts = TrackStore(ws.catalog_db)
    by_vid: dict = {}
    for t in tracks:
        by_vid.setdefault(t.video_id, []).append(t)
    for vid_id, ts_list in by_vid.items():
        ts.replace_tracks(vid_id, ts_list)
    ts.close()
    return str(tmp_path)


ARGS = ["--from", "2026-08-11T00:00", "--to", "2026-08-11T12:00",
        "--tz", "America/Los_Angeles"]


def test_count_prints_per_camera_table_provenance_and_caveats(workdir, capsys):
    rc = main(["--workdir", workdir, "aggregate", "count", "cars", *ARGS])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nvr-ch1        2" in out
    assert "nvr-ch2        2" in out
    assert "total          4" in out
    assert "plural-strip" in out and "per-window tracks" in out
    assert "caveat:" in out and "UPPER BOUND" in out
    assert "America/Los_Angeles" in out


def test_count_camera_filter(workdir, capsys):
    rc = main(["--workdir", workdir, "aggregate", "count", "cars", *ARGS,
               "--camera", "nvr-ch2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "total          2" in out
    assert "nvr-ch1" not in out.split("resolution:")[0]


def test_events_lists_the_rows_local_wallclock(workdir, capsys):
    rc = main(["--workdir", workdir, "aggregate", "events", "cars", *ARGS])
    out = capsys.readouterr().out
    assert rc == 0
    assert "4 event(s)" in out
    # e1 at exactly local midnight, rendered in the window's tz
    assert "2026-08-11T00:00:00-07:00" in out
    # b1 ten seconds before local noon
    assert "2026-08-11T11:59:50-07:00" in out


def test_histogram_sums_and_buckets(workdir, capsys):
    rc = main(["--workdir", workdir, "aggregate", "histogram", "cars", *ARGS,
               "--bucket", "6h"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "4 total across 2 bucket(s) of 6h" in out
    assert "caveat:" in out          # the method prints with every subcommand


def test_events_truncation_discloses_the_untruncated_total(workdir, capsys):
    """--limit below the event count must print 'first N of TOTAL', never let
    the capped row count read as the answer (review r3), and still print the
    caveats."""
    rc = main(["--workdir", workdir, "aggregate", "events", "cars", *ARGS,
               "--limit", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "4 event(s) — showing first 2 of 4" in out
    assert "caveat:" in out and "UPPER BOUND" in out


def test_missing_tz_is_a_hard_argparse_error(workdir, capsys):
    with pytest.raises(SystemExit):
        main(["--workdir", workdir, "aggregate", "count", "cars",
              "--from", "2026-08-11T00:00", "--to", "2026-08-11T12:00"])
    assert "--tz" in capsys.readouterr().err


def test_bad_time_and_bad_bucket_fail_with_guidance(workdir, capsys):
    with pytest.raises(SystemExit, match="unparseable time"):
        main(["--workdir", workdir, "aggregate", "count", "cars",
              "--from", "noonish", "--to", "2026-08-11T12:00", "--tz", "UTC"])
    with pytest.raises(SystemExit, match="invalid histogram bucket"):
        main(["--workdir", workdir, "aggregate", "histogram", "cars", *ARGS,
              "--bucket", "1w"])


def test_unknown_tz_fails_closed(workdir):
    with pytest.raises(SystemExit, match="invalid window"):
        main(["--workdir", workdir, "aggregate", "count", "cars",
              "--from", "2026-08-11T00:00", "--to", "2026-08-11T12:00",
              "--tz", "America/Not_A_City"])


def test_aev_only_workdir_prints_not_applicable_not_a_bare_zero(tmp_path, capsys):
    """Batch-review major: `va aggregate count` on a pure A-EV workdir (car
    tracks, no start_epoch anywhere) must disclose NOT APPLICABLE, never a
    bare 'total 0'."""
    ws = Workspace(str(tmp_path))
    video = Video(source_type=SourceType.local, source_uri="/aev",
                  source_key="aev", camera_id=None, start_epoch=None,
                  duration_seconds=120.0, ingest_status=IngestStatus.done)
    cat = Catalog(ws.catalog_db)
    cat.upsert(video)
    cat.close()
    ts = TrackStore(ws.catalog_db)
    ts.replace_tracks(video.id, [_track(video.id, "car", 10.0)])
    ts.close()

    rc = main(["--workdir", str(tmp_path), "aggregate", "count", "cars", *ARGS])
    out = capsys.readouterr().out
    assert rc == 0
    assert "total          0" in out
    assert "NOT APPLICABLE" in out
    assert "1 matched 'cars' track(s)" in out and "EXCLUDED" in out


def test_help_documents_the_tz_requirement(capsys):
    with pytest.raises(SystemExit):
        main(["aggregate", "--help"])
    out = capsys.readouterr().out
    assert "--tz is REQUIRED" in out
    assert "caveats" in out
