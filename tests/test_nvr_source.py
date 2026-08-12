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
    NvrRecordedSource,
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


def test_nvr_windows_default_to_the_security_profile():
    from va.configuration import default_footage_profile
    assert default_footage_profile("nvr_recorded") == "security"
    assert default_footage_profile("local") == "generic"


# --- pure pull plumbing ------------------------------------------------------

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


def test_torn_frame_jpeg_reads_as_undecodable_not_a_crash(tmp_path):
    # Round-5/6 review lineage + round-1 finding 3 of this branch: a >500-byte
    # but corrupt frame JPEG (ffmpeg died mid-write) must be marked None —
    # undecodable — rather than raise out of _frame_hashes, and it must NOT be
    # an all-zeros hash, because all-zeros IS the real dhash of a dark/uniform
    # frame. ffmpeg on the garbage ts writes no frames, so the pre-seeded
    # corrupt jpg is exactly what the loop sees.
    ts = tmp_path / "window.dav"
    ts.write_bytes(b"not a video")
    fdir = tmp_path / "window.frames"
    fdir.mkdir()
    (fdir / "f_0001.jpg").write_bytes(b"\xff\xd8" + b"x" * 700)
    hs = NvrRecordedSource()._frame_hashes(ts)
    assert hs == [None]


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


# --- lighting-independent verification (2026-08-10) --------------------------
# The live-snapshot reference was rejecting good footage: measured on the real
# device, a night window lost 3 of 4 chunks while the data itself scored purity
# 1.000 against its own consensus. These pin the replacement.

def _scene(seed: int, n: int = 64):
    """A deterministic 64-bit 'scene' hash."""
    import numpy as np
    rng = np.random.default_rng(seed)
    return rng.integers(0, 2, n, dtype=bool)


def _shift(scene, bits: int):
    """Same scene, `bits` bits different — simulates a lighting change."""
    import numpy as np
    out = np.array(scene, dtype=bool)
    out[:bits] = ~out[:bits]
    return out


def test_consensus_ignores_a_contaminated_minority():
    """Stale lead-in must not drag the reference toward itself."""
    from va.sources.nvr import consensus_hash, hamming
    real, junk = _scene(1), _scene(99)
    frames = [junk, junk] + [real] * 10          # 2 bad frames at the head

    cons = consensus_hash(frames)

    assert hamming(cons, real) == 0, "consensus should be the majority scene"


def test_self_distances_flag_the_lead_in_only():
    from va.sources.nvr import longest_clean_run, self_distances
    real, junk = _scene(2), _scene(77)
    frames = [junk, junk] + [real] * 10

    d = self_distances(frames)
    run = longest_clean_run(d)

    assert d[0] > 18 and d[1] > 18, "the contaminated head must read dirty"
    assert run == (2, 11), f"the clean run should skip the lead-in, got {run}"


def test_a_two_mode_library_judges_each_mode_against_its_own_entry(tmp_path):
    """Library unit behavior: once both modes are held, each verifies against
    its own entry (day->IR measured 38 bits apart, beyond the 18 gate). How a
    second mode ENTERS the library in production is pinned separately below,
    through _pull_window — building this state by hand proves nothing about
    the shipped flow (CLAUDE.md 2026-08-08 lesson, review round 1)."""
    from va.sources.nvr import ReferenceLibrary
    lib = ReferenceLibrary(tmp_path / "refs")
    day = _scene(3)
    night = _shift(day, 38)                      # measured day->IR distance

    lib.add(1, day)
    ok_before, best_before = lib.accepts(1, night)
    lib.add(1, night)
    ok_after, _ = lib.accepts(1, night)

    assert not ok_before and best_before == 38, "night must not match a day entry"
    assert ok_after, "once seeded, night footage verifies against the night entry"
    assert lib.accepts(1, day)[0], "and daylight still verifies"


def test_library_is_per_channel_so_another_camera_is_refused(tmp_path):
    """A pooled library would accept ch3's footage for a ch0 request — every
    camera here is 'ours'. Keying per channel is what makes the check mean
    anything."""
    from va.sources.nvr import ReferenceLibrary
    lib = ReferenceLibrary(tmp_path / "refs")
    ch0, ch3 = _scene(4), _scene(5)              # different cameras
    lib.add(0, ch0)
    lib.add(3, ch3)

    assert not lib.accepts(0, ch3)[0], "ch3's scene must not pass as ch0"
    assert not lib.accepts(3, ch0)[0]
    assert lib.accepts(0, ch0)[0] and lib.accepts(3, ch3)[0]


def test_first_sight_seeds_and_is_reported_as_unverified(tmp_path):
    from va.sources.nvr import ReferenceLibrary
    lib = ReferenceLibrary(tmp_path / "refs")
    s = _scene(6)

    ok, best = lib.accepts(2, s)
    assert ok and best is None, "an unseeded channel reports no comparison made"

    lib.add(2, s)
    assert lib.accepts(2, s) == (True, 0)


def test_library_survives_a_corrupt_file(tmp_path):
    """A cache must never break a pull — it is rebuildable by construction."""
    from va.sources.nvr import ReferenceLibrary
    lib = ReferenceLibrary(tmp_path / "refs")
    lib.add(1, _scene(7))
    (tmp_path / "refs" / "ch1.json").write_text("{ not json")

    ok, best = lib.accepts(1, _scene(8))

    assert ok and best is None, "unreadable library degrades to first-sight"


def test_library_is_bounded_and_deduped(tmp_path):
    from va.sources.nvr import ReferenceLibrary
    lib = ReferenceLibrary(tmp_path / "refs")
    base = _scene(9)
    for _ in range(5):
        lib.add(1, base)                          # same mode, repeatedly
    assert len(lib.load(1)) == 1, "one entry per lighting mode, not per pull"

    for i in range(1, ReferenceLibrary.MAX_PER_CHANNEL + 6):
        lib.add(1, _shift(base, 20 + i))          # many distinct modes
    assert len(lib.load(1)) <= ReferenceLibrary.MAX_PER_CHANNEL


# --- the production pull flow (review round 1: findings must be pinned through
# --- _pull_window itself, not through hand-built library states) -------------

def _pull_harness(monkeypatch, tmp_path, frames, snapshot="unset"):
    """A _pull_window call with everything device-shaped stubbed out.

    `frames` is what _frame_hashes reports; `snapshot` is what the live
    snapshot hashes to (None = snapshot unavailable). Returns (call, lib, out)
    where call() runs the pull.
    """
    from va.sources.nvr import NvrRecordedSource, ReferenceLibrary

    monkeypatch.setattr(NvrRecordedSource, "_conn",
                        staticmethod(lambda: ("http://nvr.test", "u", "p")))
    monkeypatch.setattr(NvrRecordedSource, "_stop_load",
                        lambda self, chan: None)
    monkeypatch.setattr(NvrRecordedSource, "_fetch_window",
                        lambda self, c, s, e, w: w / "window.dav")
    monkeypatch.setattr(NvrRecordedSource, "_frame_hashes",
                        lambda self, p: list(frames))
    monkeypatch.setattr(NvrRecordedSource, "_snapshot_hash",
                        lambda self, chan, work: snapshot, raising=False)
    monkeypatch.setattr(
        NvrRecordedSource, "_trim_encode",
        lambda self, raw, a, b, part: part.write_bytes(b"x" * 4096),
        raising=False)

    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    lib = ReferenceLibrary(tmp_path / "nvr_refs")
    out = cache / "out.mp4"
    t0 = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 8, 10, 1, 1, tzinfo=timezone.utc)
    src = NvrRecordedSource()
    return (lambda chan=1: src._pull_window(chan, t0, t1, out, lib)), lib, out


def test_the_regression_a_night_pull_on_a_day_seeded_channel_verifies(
        monkeypatch, tmp_path):
    """THE REGRESSION, through the production path. Channel first seen in
    daylight; a night pull must still be ingestable. The live snapshot (taken
    at pull time, i.e. at night) vouches for the new mode — it is reliably the
    right camera, and its lighting matches footage pulled near-real-time."""
    day = _scene(3)
    night = _shift(day, 38)                      # measured day->IR distance
    call, lib, out = _pull_harness(monkeypatch, tmp_path,
                                   frames=[night] * 12, snapshot=night)
    lib.add(1, day)                              # channel was day-seeded

    call()

    assert out.exists(), "the night pull must produce a clip"
    assert len(lib.load(1)) == 2, "the night mode must now be in the library"
    assert lib.accepts(1, night)[0], "later night pulls verify off the library"


def test_an_unknown_scene_matching_neither_library_nor_live_view_is_refused(
        monkeypatch, tmp_path):
    """Wrong-camera footage fails BOTH checks — that is what keeps the
    snapshot fallback from weakening the identity gate."""
    import pytest as _pytest
    day, night = _scene(3), _shift(_scene(3), 38)
    wrong_cam = _scene(55)
    call, lib, out = _pull_harness(monkeypatch, tmp_path,
                                   frames=[wrong_cam] * 12, snapshot=night)
    lib.add(1, day)

    with _pytest.raises(RuntimeError, match="neither"):
        call()
    assert not out.exists()
    assert len(lib.load(1)) == 1, "a refused pull must not touch the library"


def test_snapshot_unavailable_on_a_new_mode_refuses_with_guidance(
        monkeypatch, tmp_path):
    """Backfilling an unseen lighting mode with no live corroboration cannot
    be verified — the error must tell the operator how to recover, because
    the failure persists until the mode is seeded."""
    import pytest as _pytest
    day = _scene(3)
    night = _shift(day, 38)
    call, lib, _ = _pull_harness(monkeypatch, tmp_path,
                                 frames=[night] * 12, snapshot=None)
    lib.add(1, day)

    with _pytest.raises(RuntimeError, match="nvr_refs"):
        call()


def test_a_failed_first_pull_does_not_poison_the_channel(monkeypatch, tmp_path):
    """Review round 1 finding 4: seeding must happen only AFTER the pull
    passes verification — a garbage first pull that dies on 'no clean run'
    must leave the library empty, not become the channel's reference."""
    import pytest as _pytest
    garbage = [_scene(i) for i in range(12)]     # internally inconsistent
    call, lib, _ = _pull_harness(monkeypatch, tmp_path,
                                 frames=garbage, snapshot=None)

    with _pytest.raises(RuntimeError, match="clean run"):
        call()
    assert lib.load(1) == [], "a failed pull must not seed the library"


def test_first_sight_still_seeds_through_the_production_path(
        monkeypatch, tmp_path):
    night = _scene(4)
    call, lib, out = _pull_harness(monkeypatch, tmp_path,
                                   frames=[night] * 12, snapshot=None)

    call()

    assert out.exists()
    assert len(lib.load(1)) == 1, "first sight seeds after a successful pull"


def test_the_seed_comes_from_the_clean_run_not_the_contaminated_window(
        monkeypatch, tmp_path):
    """A first-sight pull WITH a stale lead-in must seed the scene the clean
    run agrees on — not a consensus dragged by frames that were trimmed away."""
    import numpy as np
    from va.sources.nvr import hamming
    real, junk = _scene(5), _scene(66)
    call, lib, _ = _pull_harness(monkeypatch, tmp_path,
                                 frames=[junk, junk] + [real] * 10,
                                 snapshot=None)

    call()

    seeded = np.asarray(lib.load(1)[0], dtype=bool)
    assert hamming(seeded, real) == 0, "the seed must be the clean run's scene"


# --- undecodable frames (review round 1 finding 3: the all-zeros sentinel
# --- IS the dhash of any dark/uniform frame, so it must not exist) -----------

def test_undecodable_frames_read_dirty_even_on_dark_footage(monkeypatch,
                                                            tmp_path):
    """dhash of a black frame is all zeros. A torn frame must therefore carry
    a marker that can NEVER equal a real hash — on night footage an all-zeros
    sentinel would sit INSIDE the clean run and defeat the whole check."""
    import numpy as np
    from va.sources.nvr import longest_clean_run, self_distances
    dark = np.zeros(64, dtype=bool)              # a real, valid night hash
    frames = [None, None] + [dark] * 10          # 2 torn frames at the head

    d = self_distances(frames)
    run = longest_clean_run(d)

    assert d[0] > 18 and d[1] > 18, "torn frames must read dirty on dark footage"
    assert run == (2, 11), "the clean run must exclude the torn frames"


def test_a_pull_of_mostly_undecodable_frames_is_refused(monkeypatch, tmp_path):
    """Disk-full / mass decode failure must abort the pull — undecodable
    frames do not count toward the 'enough to verify' floor, and a garbage
    pull must never reach the library."""
    import numpy as np
    import pytest as _pytest
    dark = np.zeros(64, dtype=bool)
    frames = [None] * 10 + [dark] * 2            # only 2 real frames

    call, lib, _ = _pull_harness(monkeypatch, tmp_path,
                                 frames=frames, snapshot=None)

    with _pytest.raises(RuntimeError, match="decodable"):
        call()
    assert lib.load(1) == []


def test_an_unwritable_library_never_fails_a_finished_pull(tmp_path):
    """Round-2 review minor 1: by seeding time the verified clip has already
    landed — a failed library WRITE (read-only subtree, ENOSPC) must warn and
    leave the channel unseeded, not raise out of _pull_window and fail an
    ingest whose output is already cached. load() swallows corruption; add()
    must match."""
    from va.sources.nvr import ReferenceLibrary
    blocker = tmp_path / "refs"
    blocker.write_text("a file where the library dir should go")  # mkdir fails
    lib = ReferenceLibrary(blocker)

    lib.add(1, _scene(10))                           # must not raise

    assert lib.load(1) == [], "the channel simply stays unseeded"


def test_the_default_library_lives_beside_the_cache_in_the_workdir(
        monkeypatch, tmp_path):
    """Round-3 review minor: production never injects a library — fetch()
    calls _pull_window with 4 args and the default derivation places it at
    <workdir>/nvr_refs (BESIDE the transient cache/, which out_mp4 lives in,
    so a cache wipe cannot reset every channel to first-sight trust). Nothing
    else pins that location: if a refactor moved the cache dir, the library
    would silently relocate, every channel would reset to unverified
    first-sight, and pulls would keep succeeding."""
    from va.sources.nvr import NvrRecordedSource
    night = _scene(11)
    # harness only for the stubs; its injected library goes UNUSED
    _pull_harness(monkeypatch, tmp_path, frames=[night] * 12, snapshot=None)
    out = tmp_path / "cache" / "out.mp4"
    t0 = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 8, 10, 1, 1, tzinfo=timezone.utc)

    NvrRecordedSource()._pull_window(3, t0, t1, out)   # the production call shape

    assert (tmp_path / "nvr_refs" / "ch3.json").exists(), \
        "the default library must land at <workdir>/nvr_refs/ch<N>.json"


def test_a_truncated_download_is_discarded_not_ingested_short(
        monkeypatch, tmp_path):
    """Round-1 review minor 2 (dav-direct branch): a transfer killed by
    --max-time (curl exit 28) leaves a partial .dav big enough to pass the
    size gate — it must be discarded and retried, never returned as a
    silently short clip."""
    from va.sources.nvr import NvrRecordedSource

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
