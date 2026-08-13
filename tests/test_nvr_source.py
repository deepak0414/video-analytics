"""WS4.c — nvr_recorded chunk source: URI parsing, identity, the deterministic
pad + PTS-cut pull flow (device layer stubbed), and the end-to-end ingest
oracle with the DEVICE layer stubbed (a synthetic clip stands in for the pulled
window; the live pull is validated separately against the real LNR608, like
WS4.a2)."""
import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from va.contracts.video import SourceType
from va.sources.base import resolve_source
from va.sources.nvr import (
    MAX_TRIES,
    PAD_POST,
    PAD_PRE,
    NvrRecordedSource,
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


def test_nvr_windows_default_to_the_security_profile():
    from va.configuration import default_footage_profile
    assert default_footage_profile("nvr_recorded") == "security"
    assert default_footage_profile("local") == "generic"


# --- pure pull plumbing ------------------------------------------------------

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


def test_conn_requires_env(monkeypatch):
    for var in ("VA_NVR_HOST", "VA_NVR_USER", "VA_NVR_PASS"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="VA_NVR_HOST"):
        NvrRecordedSource._conn()


# --- the deterministic pad + PTS-cut pull (2026-08-12) -----------------------
# Validated against the real device: 7/7 windows across every lighting mode had
# the target window clean behind a 10 s pre-pad, and the same window pulled
# twice was byte-identical. The pull is now pure timestamp arithmetic plus a
# duration sanity check — these tests pin that flow with the device stubbed.

T0 = datetime(2026, 8, 10, 1, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 10, 1, 0, 30, tzinfo=timezone.utc)   # a 30 s window


def _det_harness(monkeypatch, tmp_path, probe_durations=(30.0,)):
    """_pull_window with everything device-shaped stubbed out.

    `probe_durations` is what the cut sanity probe reports per attempt (the
    last value repeats). Returns (src, out, fetches, cuts) where `fetches`
    records the datetimes given to _fetch_window and `cuts` the offsets given
    to _trim_encode.
    """
    monkeypatch.setattr(NvrRecordedSource, "_conn",
                        staticmethod(lambda: ("http://nvr.test", "u", "p")))
    monkeypatch.setattr(NvrRecordedSource, "_stop_load",
                        lambda self, chan: None)
    fetches, cuts, probes = [], [], list(probe_durations)

    def fake_fetch(self, chan, start, end, work):
        fetches.append((chan, start, end))
        dav = work / "window.dav"
        dav.write_bytes(b"d" * 4096)
        return dav

    def fake_cut(self, raw, a, b, part):
        cuts.append((a, b))
        part.write_bytes(b"x" * 4096)

    def fake_probe(self, part):
        return probes.pop(0) if len(probes) > 1 else probes[0]

    monkeypatch.setattr(NvrRecordedSource, "_fetch_window", fake_fetch)
    monkeypatch.setattr(NvrRecordedSource, "_trim_encode", fake_cut)
    monkeypatch.setattr(NvrRecordedSource, "_probe_cut", fake_probe)
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    return NvrRecordedSource(), cache / "out.mp4", fetches, cuts


def test_pull_fetches_the_padded_bounds(monkeypatch, tmp_path):
    """The fetch must ask the device for [start-PAD_PRE, end+PAD_POST] — the
    pre-pad is what absorbs the §5d seek lead-in deterministically."""
    src, out, fetches, _ = _det_harness(monkeypatch, tmp_path)

    src._pull_window(1, T0, T1, out)

    assert fetches == [(1,
                        T0 - timedelta(seconds=PAD_PRE),
                        T1 + timedelta(seconds=PAD_POST))]


def test_cut_offsets_are_the_pad_and_pad_plus_window(monkeypatch, tmp_path):
    """The PTS cut is timestamp arithmetic, not a fingerprint-derived bound:
    exactly [PAD_PRE, PAD_PRE + window_len] into the padded pull."""
    src, out, _, cuts = _det_harness(monkeypatch, tmp_path)

    src._pull_window(1, T0, T1, out)

    assert cuts == [(PAD_PRE, PAD_PRE + 30.0)]


@pytest.mark.parametrize("bad_dur", [None, 5.0], ids=["undecodable", "short"])
def test_a_bad_cut_is_retried_and_raises_after_both_phases(monkeypatch, tmp_path,
                                                           bad_dur):
    """A cut with no decodable frames, or one far shorter than the window,
    retries the whole pull; when BOTH the padded phase AND the exact-window
    fallback are exhausted it raises — fail closed, never a silently short
    clip."""
    src, out, fetches, cuts = _det_harness(monkeypatch, tmp_path,
                                           probe_durations=(bad_dur,))

    with pytest.raises(RuntimeError, match="neither a padded"):
        src._pull_window(1, T0, T1, out)

    assert len(fetches) == 2 * MAX_TRIES, "padded phase then exact-window phase"
    assert len(cuts) == 2 * MAX_TRIES, "each attempt of each phase re-cuts"
    assert not out.exists(), "a failed pull must never land a clip"


def test_a_transiently_bad_cut_recovers_on_retry(monkeypatch, tmp_path):
    src, out, fetches, _ = _det_harness(monkeypatch, tmp_path,
                                        probe_durations=(None, 30.0))

    src._pull_window(1, T0, T1, out)

    assert out.exists()
    assert len(fetches) == 2, "one failed attempt, then a clean re-pull"
    # still the PADDED phase — the fallback only engages after MAX_TRIES fail
    assert fetches[-1] == (1, T0 - timedelta(seconds=PAD_PRE),
                           T1 + timedelta(seconds=PAD_POST))


def test_falls_back_to_exact_window_when_the_padded_phase_is_exhausted(
        monkeypatch, tmp_path):
    """Ring-edge recovery: when the pre-pad predates available footage the
    padded cut can never land, so after MAX_TRIES the pull re-fetches the EXACT
    window [start,end] with NO pad and cuts [0, window_len] — aligned by
    construction. This is the interaction that previously wedged a camera's
    watermark; the fallback recovers footage the padded pull can't."""
    # MAX_TRIES padded failures, then the exact-window attempt succeeds.
    src, out, fetches, cuts = _det_harness(
        monkeypatch, tmp_path,
        probe_durations=tuple([None] * MAX_TRIES + [30.0]))

    src._pull_window(1, T0, T1, out)

    assert out.exists(), "the exact-window fallback must recover the pull"
    assert len(fetches) == MAX_TRIES + 1
    assert fetches[0] == (1, T0 - timedelta(seconds=PAD_PRE),
                          T1 + timedelta(seconds=PAD_POST)), "phase 1 is padded"
    assert fetches[-1] == (1, T0, T1), "fallback fetches the exact window, no pad"
    assert cuts[-1] == (0.0, 0.0 + 30.0), "fallback cuts from offset 0"


def test_a_good_pull_lands_out_mp4_atomically(monkeypatch, tmp_path):
    """A validated cut is renamed into place; intermediates are cleaned up."""
    src, out, _, _ = _det_harness(monkeypatch, tmp_path,
                                  probe_durations=(29.1,))   # within ±2.0 s

    src._pull_window(1, T0, T1, out)

    assert out.exists() and out.stat().st_size > 0
    leftovers = [p for p in out.parent.iterdir() if p != out]
    assert leftovers == [], "the pull-private temp dir must be removed"


def test_probe_cut_measures_a_real_clip_and_rejects_garbage(tmp_path):
    """The sanity probe against reality: a real 6 s clip measures ~6 s; a
    file with no decodable frames reads None (which fails the pull)."""
    from va.media.synth import write_color_video

    clip = write_color_video(tmp_path / "clip.mp4",
                             [("grey", (128, 128, 128), 6.0)], fps=10)
    dur = NvrRecordedSource()._probe_cut(Path(clip))
    assert dur is not None and abs(dur - 6.0) <= 0.5

    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"z" * 5000)                # past the size gate, no video
    assert NvrRecordedSource()._probe_cut(junk) is None
    assert NvrRecordedSource()._probe_cut(tmp_path / "absent.mp4") is None


def test_a_truncated_download_is_discarded_not_ingested_short(
        monkeypatch, tmp_path):
    """Round-1 review minor 2 (dav-direct branch): a transfer killed by
    --max-time (curl exit 28) leaves a partial .dav big enough to pass the
    size gate — it must be discarded and retried, never returned as a
    silently short clip."""
    monkeypatch.setattr(NvrRecordedSource, "_conn",
                        staticmethod(lambda: ("http://nvr.test", "u", "p")))
    monkeypatch.setattr(NvrRecordedSource, "_stop_load", lambda self, c: None)
    monkeypatch.setattr("va.sources.nvr.time", type("T", (), {
        "sleep": staticmethod(lambda s: None)})())
    calls = []

    def fake_curl(self, url, out, max_time=60):
        if "startLoad" not in url:
            return 0
        calls.append(url)
        Path(out).write_bytes(b"x" * 5000)       # partial but past the gate
        return 28                                 # curl: --max-time hit

    monkeypatch.setattr(NvrRecordedSource, "_curl", fake_curl)
    t0 = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 8, 10, 1, 1, tzinfo=timezone.utc)

    got = NvrRecordedSource()._fetch_window(1, t0, t1, tmp_path)

    assert got is None, "a truncated download must not be returned"
    assert len(calls) == 4, "each attempt should retry, none should succeed"
    assert not (tmp_path / "window.dav").exists(), "partial file cleaned up"


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
