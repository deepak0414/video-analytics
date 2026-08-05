"""WS4.c — nvr_recorded chunk source: URI parsing, identity, verify-and-trim
plumbing (pure functions), and the end-to-end ingest oracle with the DEVICE
layer stubbed (a synthetic clip stands in for the pulled window; the live pull
is validated separately against the real LNR608, like WS4.a2)."""
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from va.contracts.video import SourceType
from va.sources.base import resolve_source
from va.sources.nvr import (
    CHUNK_S,
    NvrRecordedSource,
    chunk_bounds,
    longest_clean_run,
    parse_nvr_uri,
)

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"
URI = "nvr://1/2026-08-01T12:00:00/2026-08-01T12:00:30"


# --- URI + identity ----------------------------------------------------------

def test_parse_uri_utc(monkeypatch):
    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    chan, start, end = parse_nvr_uri(URI)
    assert chan == 1
    assert start == datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert (end - start).total_seconds() == 30


def test_parse_uri_respects_nvr_timezone(monkeypatch):
    monkeypatch.setenv("VA_NVR_TZ", "America/Los_Angeles")
    _, start, _ = parse_nvr_uri(URI)
    # 12:00 PDT (UTC-7 on 2026-08-01) == 19:00 UTC
    assert start == datetime(2026, 8, 1, 19, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", [
    "nvr://x/2026-08-01T12:00:00/2026-08-01T12:00:30",       # non-numeric channel
    "nvr://1/notatime/2026-08-01T12:00:30",                   # junk time
    "nvr://1/2026-08-01T12:00:30/2026-08-01T12:00:00",        # end before start
    "nvr://1/2026-08-01T12:00:00/2026-08-01T13:00:00",        # window > cap
    "nvr://1/2026-08-01T12:00:00",                            # missing end
])
def test_bad_uris_raise(bad, monkeypatch):
    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    with pytest.raises(ValueError):
        parse_nvr_uri(bad)


def test_resolve_identity_and_placement(monkeypatch):
    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    r = NvrRecordedSource().resolve(URI)
    assert r.source_type is SourceType.nvr_recorded
    s = int(datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
    assert r.source_key == f"nvr:ch1:{s}-{s + 30}"
    assert r.start_epoch == float(s)
    assert (r.camera.id, r.camera.source_ref) == ("nvr-ch1", "1")
    # same window re-resolved -> same identity (dedup key stability)
    assert NvrRecordedSource().resolve(URI).source_key == r.source_key


def test_stored_uri_is_canonical_utc_and_env_independent(monkeypatch):
    # Round-5 review: a naive URI's identity depends on VA_NVR_TZ at resolve
    # time; the STORED uri must re-resolve identically in any environment.
    monkeypatch.setenv("VA_NVR_TZ", "America/Los_Angeles")
    first = NvrRecordedSource().resolve(URI)
    assert "+00:00" in first.source_uri
    monkeypatch.setenv("VA_NVR_TZ", "Asia/Kolkata")   # totally different env
    again = NvrRecordedSource().resolve(first.source_uri)
    assert again.source_key == first.source_key
    assert again.source_uri == first.source_uri


def test_motion_source_config_is_part_of_scene_provenance(tmp_path, monkeypatch):
    # Round-5 review major: motion-episode segments are a function of where
    # motion windows come from — switching/reconfiguring motion_source must
    # change the scene_detector fingerprint (else a missed stale, §6-b).
    # NB: a twin of this test ships in tests/test_motion_scene_detector.py —
    # the fix was cherry-picked down to WS4.b so that branch merged standalone,
    # and the test-deletion guard (rightly) prefers redundant coverage over a
    # net test removal here.
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


def test_dispatch_picks_nvr_source():
    assert isinstance(resolve_source(URI), NvrRecordedSource)


def test_nvr_chunks_default_to_the_security_profile():
    from va.configuration import default_footage_profile
    assert default_footage_profile("nvr_recorded") == "security"
    assert default_footage_profile("local") == "generic"


# --- pure pull plumbing ------------------------------------------------------

def test_chunk_bounds_covers_window_with_clipped_tail(monkeypatch):
    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    _, start, end = parse_nvr_uri("nvr://1/2026-08-01T12:00:00/2026-08-01T12:00:25")
    bounds = chunk_bounds(start, end)
    assert [(int((b - start).total_seconds()), int((e - start).total_seconds()))
            for b, e in bounds] == [(0, 10), (10, 20), (20, 25)]
    assert all((e - b).total_seconds() <= CHUNK_S for b, e in bounds)


def test_longest_clean_run_trims_stale_leadin():
    # the measured signature: ~1 s stale lead-in (Hamming ~31-37), clean rest
    hs = [31, 35, 8, 9, 7, 10, 9, 8]
    assert longest_clean_run(hs) == (2, 7)
    assert longest_clean_run([30, 29, 40]) is None          # nothing clean
    assert longest_clean_run([5, 6, 30, 7, 8, 9]) == (3, 5)  # longest, not first


def test_curl_argv_never_carries_credentials():
    # Round-2 review: creds on the argv are readable by any local user via the
    # process list during a pull; they must travel via `--config -` on stdin.
    argv = NvrRecordedSource._curl_argv("http://nvr.test/x", "/dev/null", 30)
    assert "--config" in argv and "-u" not in argv
    # nothing user:pass-shaped anywhere on the command line (URL aside)
    assert all(":" not in a or a.startswith("http") for a in argv)


def test_curl_config_escapes_quote_and_backslash():
    # curl's config parser interprets \ and " inside quoted values — a
    # password containing either must arrive escaped, not silently mangled.
    line = NvrRecordedSource._curl_config("admin", 'p"a\\ss')
    assert line == 'user = "admin:p\\"a\\\\ss"\n'


def test_curl_config_rejects_newlines_loudly():
    # A newline would terminate the quoted value mid-credential and 401 every
    # transfer with misleading downstream errors — refuse up front.
    with pytest.raises(RuntimeError, match="newline"):
        NvrRecordedSource._curl_config("admin", "pa\nss")


def test_torn_frame_jpeg_counts_dirty_not_fatal(tmp_path):
    # Round-5/6 review: a >500-byte but corrupt frame JPEG (ffmpeg died
    # mid-write) must count as DIRTY inside the retry machinery, not raise out
    # of _frame_hammings and abort the ingest. ffmpeg on the garbage ts writes
    # no frames, so the pre-seeded corrupt jpg is exactly what the loop sees.
    import numpy as np

    ts = tmp_path / "chunk.ts"
    ts.write_bytes(b"not a video")
    fdir = tmp_path / "chunk.frames"
    fdir.mkdir()
    (fdir / "f_001.jpg").write_bytes(b"\xff\xd8" + b"x" * 700)
    refh = np.zeros(64, dtype=bool)
    hs = NvrRecordedSource()._frame_hammings(ts, refh)
    assert hs == [10**6]


def test_conn_requires_env(monkeypatch):
    for var in ("VA_NVR_HOST", "VA_NVR_USER", "VA_NVR_PASS"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="VA_NVR_HOST"):
        NvrRecordedSource._conn()


# --- the done-when oracle: ingest a pulled window end-to-end -----------------

def _stub_pull(tmp_path, monkeypatch):
    """Device layer stub: 'pull' = write a synthetic clip where the mp4 goes."""
    from va.media.synth import write_color_video

    def fake_pull(self, chan, start, end, out_mp4):
        src = write_color_video(tmp_path / "pulled_src.mp4",
                                [("grey", (128, 128, 128), 6.0)], fps=10)
        shutil.copy(src, out_mp4)

    monkeypatch.setattr(NvrRecordedSource, "_pull_window", fake_pull)


def test_ingest_of_pulled_window_lands_media_segments_and_epoch(
        tmp_path, monkeypatch):
    from va.pipeline.ingest import ingest

    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    _stub_pull(tmp_path, monkeypatch)

    s_epoch = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    # motion events for the window -> the security profile's motion-episodes
    # Role 1 must land exactly this (padded) segment
    events_file = tmp_path / "motion.json"
    events_file.write_text(json.dumps({"events": [
        {"camera_ref": "1", "start_epoch": s_epoch + 2, "end_epoch": s_epoch + 3},
    ]}))
    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    sec = cdir / "profiles" / "footage" / "security.yaml"
    doc = yaml.safe_load(sec.read_text())
    doc["roles"]["motion_source"] = {"backend": "inproc", "model": "sidecar",
                                     "events_file": str(events_file)}
    doc["roles"]["scene_detector"]["pad_s"] = 1.0
    sec.write_text(yaml.safe_dump(doc))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    ws = tmp_path / ".va"
    result = ingest(URI, workdir=str(ws), fps=1.0)   # profile: source default

    v = result.video
    assert v.ingest_status.value == "done"
    assert v.profile == "security"                    # source-derived default
    assert v.start_epoch == s_epoch                   # placement on the row
    assert v.camera_id == "nvr-ch1"
    # media persisted INTO the workdir (pulled file was in cache/)
    assert Path(v.local_path).is_relative_to(ws / "videos")
    assert Path(v.local_path).exists()

    conn = sqlite3.connect(ws / "catalog.db")
    cam = conn.execute(
        "SELECT source_ref FROM cameras WHERE id = 'nvr-ch1'").fetchone()
    segs = conn.execute(
        "SELECT start_time, end_time FROM segments").fetchall()
    conn.close()
    assert cam == ("1",)
    # ground truth: event at +2..+3, pad 1 -> (1.0, 4.0)
    assert segs == [(1.0, 4.0)]
    # speech roles gated by the security profile
    assert result.transcript_lines == 0


def test_reingest_of_pulled_window_is_idempotent(tmp_path, monkeypatch):
    from va.pipeline.ingest import ingest

    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    _stub_pull(tmp_path, monkeypatch)
    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    ws = tmp_path / ".va"
    first = ingest(URI, workdir=str(ws), fps=1.0)
    again = ingest(URI, workdir=str(ws), fps=1.0)

    assert first.deduped is False and again.deduped is True
    assert again.video.id == first.video.id
    assert again.video.start_epoch == first.video.start_epoch
    conn = sqlite3.connect(ws / "catalog.db")
    (n,) = conn.execute("SELECT COUNT(*) FROM videos").fetchone()
    conn.close()
    assert n == 1


def test_reingest_reuses_preserved_media_without_repulling(tmp_path, monkeypatch):
    """Round-1 review minor: the NVR retains ~days — a reingest of an older
    window must reuse the preserved clip, never depend on a re-pull."""
    from va.pipeline.ingest import ingest
    from va.pipeline.manage import reingest_video

    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    _stub_pull(tmp_path, monkeypatch)
    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    ws = tmp_path / ".va"
    first = ingest(URI, workdir=str(ws), fps=1.0)
    assert first.video.ingest_status.value == "done"

    def dead_device(self, chan, start, end, out_mp4):
        raise RuntimeError("footage expired / device offline")

    monkeypatch.setattr(NvrRecordedSource, "_pull_window", dead_device)
    result = reingest_video(str(ws), URI, fps=1.0)
    assert result is not None and result.video.ingest_status.value == "done"
    assert Path(result.video.local_path).exists()
    assert result.video.start_epoch == first.video.start_epoch  # carried


def test_reingest_survives_deleted_camera_and_keeps_epoch(tmp_path, monkeypatch,
                                                          caplog):
    """Round-2 review minor: a camera deleted out from under a chunk must not
    crash reingest after the purge — the chunk keeps start_epoch (which Role 1
    needs) and only loses the camera link, with a warning."""
    from va.pipeline.ingest import ingest
    from va.pipeline.manage import reingest_video

    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    _stub_pull(tmp_path, monkeypatch)
    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    ws = tmp_path / ".va"
    first = ingest(URI, workdir=str(ws), fps=1.0)
    conn = sqlite3.connect(ws / "catalog.db")
    conn.execute("DELETE FROM cameras WHERE id = 'nvr-ch1'")
    conn.commit()
    conn.close()

    with caplog.at_level("WARNING"):
        result = reingest_video(str(ws), URI, fps=1.0)
    assert result is not None and result.video.ingest_status.value == "done"
    assert result.video.start_epoch == first.video.start_epoch
    assert any("no longer exists" in r.message for r in caplog.records)


def test_set_camera_rejects_unknown_camera(tmp_path):
    from va.contracts.video import ResolvedVideo
    from va.storage.structured.catalog_sqlite import Catalog

    db = tmp_path / "catalog.db"
    catalog = Catalog(db)
    video, _ = catalog.get_or_create(ResolvedVideo(
        source_type=SourceType.local, source_uri="x", source_key="sha256:abc"))
    with pytest.raises(ValueError, match="unknown camera"):
        catalog.set_camera(video.id, "ghost")
    catalog.close()


def test_camera_get_or_create_is_atomic_and_never_clobbers(tmp_path):
    from va.contracts.video import Camera
    from va.storage.structured.cameras import CameraStore

    store = CameraStore(tmp_path / "catalog.db")
    a, created_a = store.get_or_create(
        Camera(id="cam", name="user renamed me", source_ref="1"))
    b, created_b = store.get_or_create(
        Camera(id="cam", name="NVR channel 1", source_ref="1"))
    assert (created_a, created_b) == (True, False)
    assert b.name == "user renamed me"   # existing row untouched
    store.close()
