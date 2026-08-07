"""WS6.b — catch-up watcher: simulated-outage backfill is exact and
exactly-once, watermarks are durable and monotonic, catch-up is bounded, long
episodes split within the nvr window cap, and a failed window holds the
watermark for retry."""
import json
import shutil
import sqlite3
from pathlib import Path

import yaml

from va.contracts.video import Camera
from va.pipeline.paths import Workspace
from va.pipeline.watch import catch_up
from va.sources.nvr import MAX_WINDOW_S, NvrRecordedSource
from va.storage.structured.cameras import CameraStore

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"
T0 = 1_700_000_000.0


def _setup(tmp_path, monkeypatch, events, watermark=T0):
    """Workdir with one registered camera (watermark set), sidecar motion
    events, stubbed pull, and a config whose motion_source reads the sidecar."""
    from va.media.synth import write_color_video

    monkeypatch.setenv("VA_NVR_TZ", "UTC")
    events_file = tmp_path / "motion.json"
    events_file.write_text(json.dumps({"events": events}))
    cdir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cdir)
    doc = yaml.safe_load((cdir / "roles.yaml").read_text())
    doc["roles"]["motion_source"] = {"backend": "inproc", "model": "sidecar",
                                     "events_file": str(events_file)}
    (cdir / "roles.yaml").write_text(yaml.safe_dump(doc))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))

    src_clip = write_color_video(tmp_path / "pulled.mp4",
                                 [("grey", (128, 128, 128), 4.0)], fps=10)

    pulls = []

    def fake_pull(self, chan, start, end, out_mp4):
        pulls.append((chan, start.timestamp(), end.timestamp()))
        shutil.copy(src_clip, out_mp4)

    monkeypatch.setattr(NvrRecordedSource, "_pull_window", fake_pull)

    ws = tmp_path / ".va"
    ws.mkdir(exist_ok=True)
    store = CameraStore(Workspace(str(ws)).catalog_db)
    store.get_or_create(Camera(id="nvr-ch1", name="driveway", source_ref="1"))
    if watermark is not None:
        store.set_watermark("nvr-ch1", watermark)
    store.close()
    return ws, pulls


def _ev(start, end, cam="1"):
    return {"camera_ref": cam, "start_epoch": T0 + start, "end_epoch": T0 + end}


def _watermark(ws):
    store = CameraStore(Workspace(str(ws)).catalog_db)
    try:
        return store.get("nvr-ch1").last_processed_epoch
    finally:
        store.close()


def _video_count(ws):
    con = sqlite3.connect(Workspace(str(ws)).catalog_db)
    (n,) = con.execute("SELECT COUNT(*) FROM videos").fetchone()
    con.close()
    return n


def test_outage_backfills_exactly_the_gap_once(tmp_path, monkeypatch):
    """The done-when oracle: watermark at T0, two episodes in the gap, one
    beyond the horizon. Exactly the two gap episodes are pulled+ingested; a
    second pass ingests nothing more; the watermark lands at the horizon."""
    events = [
        _ev(10, 40),                       # in the gap
        _ev(100, 130),                     # in the gap
        _ev(290, 310),                     # beyond the horizon (still open)
        {"camera_ref": "2", "start_epoch": T0 + 20, "end_epoch": T0 + 50},
    ]                                      # other camera: not registered
    ws, pulls = _setup(tmp_path, monkeypatch, events)

    report = catch_up(str(ws), now_epoch=T0 + 300, settle_s=60.0)
    assert report.windows_ingested == 2
    assert _video_count(ws) == 2
    # exact windows: [10,40] and [100,130] on channel 1 only
    assert [(c, round(s - T0), round(e - T0)) for c, s, e in pulls] == [
        (1, 10, 40), (1, 100, 130)]
    assert _watermark(ws) == T0 + 240      # the horizon (300 - 60)

    report2 = catch_up(str(ws), now_epoch=T0 + 300, settle_s=60.0)
    assert report2.windows_ingested == 0   # idempotent: nothing re-pulled
    assert _video_count(ws) == 2


def test_straddling_episode_is_not_repulled(tmp_path, monkeypatch):
    """An episode that started BEFORE the watermark was covered by the cycle
    that advanced the watermark past it — only new starts count."""
    ws, pulls = _setup(tmp_path, monkeypatch,
                       [_ev(-20, 30), _ev(60, 90)], watermark=T0)
    report = catch_up(str(ws), now_epoch=T0 + 300, settle_s=60.0)
    assert report.windows_ingested == 1
    assert [(round(s - T0), round(e - T0)) for _, s, e in pulls] == [(60, 90)]


def test_max_windows_bounds_the_pass_and_resumes(tmp_path, monkeypatch):
    events = [_ev(0, 30), _ev(100, 130), _ev(200, 230)]
    ws, pulls = _setup(tmp_path, monkeypatch, events, watermark=T0 - 1)
    r1 = catch_up(str(ws), now_epoch=T0 + 400, settle_s=60.0, max_windows=2)
    assert r1.windows_ingested == 2
    assert r1.cameras[0].truncated
    assert _watermark(ws) == T0 + 130      # last COMPLETE episode, not horizon
    r2 = catch_up(str(ws), now_epoch=T0 + 400, settle_s=60.0, max_windows=2)
    assert r2.windows_ingested == 1        # the third episode
    assert _watermark(ws) == T0 + 340      # horizon


def test_long_episode_splits_within_window_cap(tmp_path, monkeypatch):
    long_end = MAX_WINDOW_S * 2 + 50       # 290 s with the 120 s cap
    ws, pulls = _setup(tmp_path, monkeypatch, [_ev(0, long_end)],
                       watermark=T0 - 1)
    report = catch_up(str(ws), now_epoch=T0 + long_end + 200, settle_s=60.0)
    assert report.windows_ingested == 3
    spans = [(round(s - T0), round(e - T0)) for _, s, e in pulls]
    assert spans == [(0, 120), (120, 240), (240, 290)]
    assert all(e - s <= MAX_WINDOW_S for s, e in spans)


def test_failed_window_holds_watermark_for_retry(tmp_path, monkeypatch):
    events = [_ev(0, 30), _ev(100, 130)]
    ws, pulls = _setup(tmp_path, monkeypatch, events, watermark=T0 - 1)

    import va.pipeline.watch as watch_mod
    from va.pipeline.ingest import ingest as real_ingest

    calls = []

    def flaky_ingest(uri, workdir):
        calls.append(uri)
        if len(calls) == 2:                # second window dies
            raise RuntimeError("device hiccup")
        return real_ingest(uri, workdir=workdir)

    monkeypatch.setattr(watch_mod, "catch_up", watch_mod.catch_up)
    import va.pipeline.ingest as ing_mod
    monkeypatch.setattr(ing_mod, "ingest", flaky_ingest)

    r1 = catch_up(str(ws), now_epoch=T0 + 300, settle_s=60.0)
    assert (r1.windows_ingested, r1.cameras[0].windows_failed) == (1, 1)
    assert _watermark(ws) == T0 + 30       # held at the last complete episode

    monkeypatch.setattr(ing_mod, "ingest", real_ingest)
    r2 = catch_up(str(ws), now_epoch=T0 + 300, settle_s=60.0)
    assert r2.windows_ingested == 1        # the failed one, retried
    assert _watermark(ws) == T0 + 240


def test_null_watermark_uses_bounded_lookback(tmp_path, monkeypatch):
    events = [_ev(-7200, -7170), _ev(-100, -70)]   # ancient vs recent
    ws, pulls = _setup(tmp_path, monkeypatch, events, watermark=None)
    report = catch_up(str(ws), now_epoch=T0, settle_s=10.0, lookback_s=600.0)
    assert report.windows_ingested == 1            # only the recent episode
    assert [(round(s - T0), round(e - T0)) for _, s, e in pulls] == [(-100, -70)]


def test_horizon_straddling_episode_defers_whole_not_clipped(tmp_path,
                                                             monkeypatch):
    """Round-1 CRITICAL repro: an episode still running at the settle horizon
    must be deferred WHOLE — clipping loses its tail, and advancing the
    watermark past its start loses it forever. Once settled, one later pass
    pulls all of it."""
    ws, pulls = _setup(tmp_path, monkeypatch, [_ev(50, 250)], watermark=T0)

    r1 = catch_up(str(ws), now_epoch=T0 + 200, settle_s=60.0)   # horizon 140
    assert r1.windows_ingested == 0            # nothing clipped out
    assert _watermark(ws) == T0 + 50           # held AT the episode start

    r2 = catch_up(str(ws), now_epoch=T0 + 400, settle_s=60.0)   # now settled
    assert r2.windows_ingested == 2            # 200 s episode -> 2 windows
    spans = [(round(s - T0), round(e - T0)) for _, s, e in pulls]
    assert spans == [(50, 170), (170, 250)]    # the WHOLE episode, once
    assert _watermark(ws) == T0 + 340


def test_open_lnr_instant_defers_not_skips(tmp_path, monkeypatch):
    """An lnr `open` zero-length instant (episode still open in the log) must
    hold the watermark, not be skipped-and-passed."""
    ws, pulls = _setup(
        tmp_path, monkeypatch,
        [{"camera_ref": "1", "start_epoch": T0 + 30, "end_epoch": T0 + 30,
          "attributes": {"open": True}}],
        watermark=T0)
    r1 = catch_up(str(ws), now_epoch=T0 + 300, settle_s=60.0)
    assert r1.windows_ingested == 0
    assert _watermark(ws) == T0 + 30           # held at the open start
    assert pulls == []


def test_giant_episode_respects_budget_and_progresses(tmp_path, monkeypatch):
    """Round-1 major: the cap binds INSIDE an episode; deduped replays on the
    next pass are free, so the episode eventually completes."""
    long_end = MAX_WINDOW_S * 4 + 10           # 5 windows
    ws, pulls = _setup(tmp_path, monkeypatch, [_ev(0, long_end)],
                       watermark=T0 - 1)
    now = T0 + long_end + 200

    r1 = catch_up(str(ws), now_epoch=now, settle_s=60.0, max_windows=2)
    assert r1.windows_ingested == 2 and r1.cameras[0].truncated
    assert _watermark(ws) == T0 - 1            # episode incomplete: held

    r2 = catch_up(str(ws), now_epoch=now, settle_s=60.0, max_windows=2)
    assert r2.windows_ingested == 2            # windows 3-4 (1-2 deduped free)
    r3 = catch_up(str(ws), now_epoch=now, settle_s=60.0, max_windows=2)
    assert r3.windows_ingested == 1            # window 5; episode completes
    assert _watermark(ws) == now - 60.0        # horizon reached
    assert len(pulls) == 5                     # each window pulled exactly once


def test_run_watch_survives_a_failing_pass(tmp_path, monkeypatch):
    """A transient catch_up failure must not kill the daemon loop."""
    import va.pipeline.watch as watch_mod

    calls = []

    def flaky_catch_up(workdir, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return watch_mod.CatchUpReport()

    monkeypatch.setattr(watch_mod, "catch_up", flaky_catch_up)
    watch_mod.run_watch(str(tmp_path / ".va"), interval_s=0.01, stop_after=2)
    assert len(calls) == 2                     # second pass ran despite crash


def test_settled_episode_after_deferred_does_not_leak_watermark(tmp_path,
                                                                monkeypatch):
    """Round-2 major: a settled episode ORDERED AFTER a deferred open instant
    must not push the watermark past the deferred start — that would filter
    the deferred episode out forever."""
    ws, pulls = _setup(
        tmp_path, monkeypatch,
        [{"camera_ref": "1", "start_epoch": T0 + 30, "end_epoch": T0 + 30,
          "attributes": {"open": True}},
         _ev(65, 90)],
        watermark=T0)
    r1 = catch_up(str(ws), now_epoch=T0 + 300, settle_s=60.0)
    assert r1.windows_ingested == 1            # the settled [65,90]
    assert _watermark(ws) == T0 + 30           # capped AT the deferred start
    # next pass (instant still young): settled episode re-pull dedups free,
    # watermark stays put — nothing lost, nothing wedged yet
    r2 = catch_up(str(ws), now_epoch=T0 + 300, settle_s=60.0)
    assert _watermark(ws) == T0 + 30


def test_stuck_open_instant_ages_out_with_recovery_pull(tmp_path, monkeypatch,
                                                        caplog):
    """Round-2 major: a lost-End open instant re-emits forever — once older
    than the age bound it must be recovered as one padded window and passed,
    loudly, instead of wedging the camera."""
    ws, pulls = _setup(
        tmp_path, monkeypatch,
        [{"camera_ref": "1", "start_epoch": T0 + 30, "end_epoch": T0 + 30,
          "attributes": {"open": True}},
         _ev(400, 430)],
        watermark=T0)
    import logging
    with caplog.at_level(logging.WARNING):
        r = catch_up(str(ws), now_epoch=T0 + 1000, settle_s=60.0,
                     open_instant_max_age_s=600.0)
    # instant is 910 s old at the horizon: recovered as [30, 30+120] + the
    # settled [400,430]
    spans = [(round(s - T0), round(e - T0)) for _, s, e in pulls]
    assert spans == [(30, 150), (400, 430)]
    assert _watermark(ws) == T0 + 940          # horizon: nothing deferred
    assert any("lost End marker" in rec.message for rec in caplog.records)


def test_unmatched_camera_filter_warns(tmp_path, monkeypatch, caplog):
    ws, pulls = _setup(tmp_path, monkeypatch, [], watermark=T0)
    import logging
    with caplog.at_level(logging.WARNING):
        catch_up(str(ws), camera_ids=["nvr-ch9"], now_epoch=T0 + 300)
    assert any("nvr-ch9" in rec.message and "NOT be watched" in rec.message
               for rec in caplog.records)


def test_open_instant_merging_into_prior_episode_still_defers(tmp_path,
                                                              monkeypatch):
    """Round-3 CRITICAL repro: a closed episode [30,60] + open instant at 70
    (gap 10 <= cluster gap 30 — the chatty-log norm) used to MERGE, losing the
    open signature; the watermark then advanced to the horizon and the real
    episode (closing later as [70,200]) was filtered out forever."""
    ws, pulls = _setup(
        tmp_path, monkeypatch,
        [_ev(30, 60),
         {"camera_ref": "1", "start_epoch": T0 + 70, "end_epoch": T0 + 70,
          "attributes": {"open": True}}],
        watermark=T0)
    r1 = catch_up(str(ws), now_epoch=T0 + 300, settle_s=60.0)
    assert r1.windows_ingested == 1
    assert [(round(s - T0), round(e - T0)) for _, s, e in pulls] == [(30, 60)]
    assert _watermark(ws) == T0 + 70       # held AT the open instant, not 240

    # The episode closes in the log as [70, 200]: the next pass pulls it whole.
    events_file = tmp_path / "motion.json"
    events_file.write_text(json.dumps({"events": [
        _ev(30, 60), _ev(70, 200)]}))
    r2 = catch_up(str(ws), now_epoch=T0 + 300, settle_s=60.0)
    assert r2.windows_ingested >= 1
    spans = [(round(s - T0), round(e - T0)) for _, s, e in pulls]
    # the 130 s episode splits at the 120 s window cap — full coverage matters
    assert (70, 190) in spans and (190, 200) in spans
    assert _watermark(ws) == T0 + 240


def test_budget_splits_per_camera_no_starvation(tmp_path, monkeypatch):
    """Round-4 review: the window budget is split per camera — a deeply
    backlogged first camera must not starve the second."""
    ws, pulls = _setup(
        tmp_path, monkeypatch,
        [_ev(0, 30), _ev(100, 130), _ev(200, 230),          # ch1 backlog
         _ev(0, 30, cam="2")],                               # ch2 has one
        watermark=None)
    store = CameraStore(Workspace(str(ws)).catalog_db)
    store.get_or_create(Camera(id="nvr-ch2", name="porch", source_ref="2"))
    store.set_watermark("nvr-ch1", T0 - 1)
    store.set_watermark("nvr-ch2", T0 - 1)
    store.close()

    r = catch_up(str(ws), now_epoch=T0 + 400, settle_s=60.0, max_windows=2)
    by_cam = {c.camera_id: c.windows_ingested for c in r.cameras}
    # 2 windows split 1+1: ch2's only episode lands despite ch1's backlog
    assert by_cam == {"nvr-ch1": 1, "nvr-ch2": 1}


def test_fractional_subsecond_event_pulls_widened_window(tmp_path, monkeypatch):
    """Round-5 review: a sub-second event must widen to a valid whole-second
    window, not wedge the camera on a start==end URI forever."""
    ws, pulls = _setup(
        tmp_path, monkeypatch,
        [{"camera_ref": "1", "start_epoch": T0 + 10.4, "end_epoch": T0 + 10.9}],
        watermark=T0)
    r = catch_up(str(ws), now_epoch=T0 + 300, settle_s=60.0)
    assert r.windows_ingested == 1
    ((_, s_, e_),) = pulls
    assert e_ - s_ >= 1.0                       # widened, never degenerate
    assert _watermark(ws) == T0 + 240           # advanced: no wedge


def test_fractional_start_long_episode_splits_within_cap(tmp_path, monkeypatch):
    """Rebase-review major: a fractional-start episode > MAX_WINDOW_S used to
    split into floor/ceil-widened cap+1-second windows that the nvr parser
    hard-rejects on every retry — a permanent wedge. Every emitted window must
    parse and stay within the cap."""
    long_end = 10.4 + MAX_WINDOW_S + 69.6          # 190 s episode, fractional start
    ws, pulls = _setup(
        tmp_path, monkeypatch,
        [{"camera_ref": "1", "start_epoch": T0 + 10.4,
          "end_epoch": T0 + long_end}],
        watermark=T0)
    r = catch_up(str(ws), now_epoch=T0 + long_end + 200, settle_s=60.0)
    assert r.cameras[0].windows_failed == 0        # nothing rejected
    assert r.windows_ingested == 2
    for _, s_, e_ in pulls:
        assert e_ - s_ <= MAX_WINDOW_S + 1e-6      # every window within cap
    assert _watermark(ws) == T0 + long_end + 140   # horizon: no wedge
