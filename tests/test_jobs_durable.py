"""WS6.a — durable job queue: submits and state transitions persist to the
`jobs` table; a restarted worker resumes queued/running INGEST jobs exactly
once (ingest() is the idempotency point); ask jobs fail on restart instead of
silently re-running."""
import json
import sqlite3
import time
from pathlib import Path

from va.pipeline.paths import Workspace
from va.storage.structured.jobs_store import JobStore
from va.web.jobs import AskQueue, IngestQueue


def _wait(pred, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _clip(tmp_path):
    from va.media.synth import write_color_video

    return write_color_video(tmp_path / "clip.mp4",
                             [("red", (220, 30, 30), 3.0)], fps=10)


def _job_rows(workdir):
    con = sqlite3.connect(Workspace(str(workdir)).catalog_db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM jobs")]
    con.close()
    return rows


def test_submit_persists_and_completes(tmp_path):
    clip = _clip(tmp_path)
    ws = tmp_path / ".va"
    q = IngestQueue(str(ws))
    q.start()
    try:
        job = q.submit(str(clip))
        assert _wait(lambda: job.state.value == "done")
    finally:
        q.stop()
    (row,) = _job_rows(ws)
    assert row["kind"] == "ingest" and row["state"] == "done"
    assert json.loads(row["payload"])["uri"] == str(clip)
    assert json.loads(row["result"])["frames_indexed"] >= 1


def test_crashed_running_job_resumes_exactly_once(tmp_path, monkeypatch):
    """The done-when oracle: a `running` row is the artifact of a worker killed
    mid-job. A restarted worker must run it to completion exactly once — and a
    second restart must not run it again."""
    clip = _clip(tmp_path)
    ws = tmp_path / ".va"

    # The crash artifact: a job the dead worker had marked running.
    store = JobStore(Workspace(str(ws)).catalog_db)
    store.record("crashed-job", "ingest", {"uri": str(clip), "fps": 1.0})
    store.update("crashed-job", "running")
    store.close()

    calls = []
    import va.web.jobs as jobs_mod
    from va.pipeline.ingest import ingest as real_ingest

    def counting_ingest(uri, workdir, fps):
        calls.append(uri)
        return real_ingest(uri, workdir=workdir, fps=fps)

    import va.pipeline.ingest as ing_mod
    monkeypatch.setattr(ing_mod, "ingest", counting_ingest)

    q = IngestQueue(str(ws))          # "restarted server"
    q.start()
    try:
        assert _wait(lambda: (q.get("crashed-job") is not None
                              and q.get("crashed-job").state.value == "done"))
    finally:
        q.stop()
    assert len(calls) == 1            # resumed exactly once
    (row,) = _job_rows(ws)
    assert row["state"] == "done"

    # Second restart: nothing pending, nothing re-runs.
    q2 = IngestQueue(str(ws))
    q2.start()
    q2.stop()
    assert len(calls) == 1
    (row,) = _job_rows(ws)
    assert row["state"] == "done"


def test_resume_after_kill_with_partial_shard_does_not_duplicate_vectors(tmp_path):
    """Round-1 review major: a kill between store.persist() and `done` leaves a
    COMPLETE-looking vector shard; the stores load-and-append, so a resume that
    reused it would double every frame embedding. Forge exactly that state
    (done ingest, then row knocked back to `processing` + jobs row `running`)
    and assert the resumed run lands the single-run vector count."""
    import numpy as np

    from va.storage.structured.catalog_sqlite import Catalog
    from va.storage.vector.numpy_flat import NumpyFlatVectorStore

    clip = _clip(tmp_path)
    ws = tmp_path / ".va"

    q = IngestQueue(str(ws))
    q.start()
    try:
        job = q.submit(str(clip))
        assert _wait(lambda: job.state.value == "done")
    finally:
        q.stop()

    (video_dir,) = list((ws / "videos").iterdir())
    baseline = len(NumpyFlatVectorStore(video_dir / "vectors")._payloads)
    assert baseline >= 1

    # Forge the mid-kill state: shard persisted, row NOT done, job `running`.
    con = sqlite3.connect(Workspace(str(ws)).catalog_db)
    con.execute("UPDATE videos SET ingest_status = 'processing'")
    con.execute("UPDATE jobs SET state = 'running'")
    con.commit()
    con.close()

    q2 = IngestQueue(str(ws))          # restarted server
    q2.start()
    try:
        jid = _job_rows(ws)[0]["id"]
        assert _wait(lambda: (q2.get(jid) is not None
                              and q2.get(jid).state.value == "done"))
    finally:
        q2.stop()

    resumed = len(NumpyFlatVectorStore(video_dir / "vectors")._payloads)
    assert resumed == baseline          # NOT doubled
    catalog = Catalog(Workspace(str(ws)).catalog_db)
    try:
        (v,) = catalog.list()
        assert v.ingest_status.value == "done"
    finally:
        catalog.close()


def test_ask_jobs_fail_on_restart_not_rerun(tmp_path, monkeypatch):
    ws = tmp_path / ".va"
    store = JobStore(Workspace(str(ws)).catalog_db)
    store.record("stale-ask", "ask", {"question": "what happened?", "k": 5})
    store.close()

    calls = []
    import va.pipeline.ask as ask_mod
    monkeypatch.setattr(ask_mod, "ask",
                        lambda *a, **k: calls.append(1))

    q = AskQueue(str(ws))
    q.start()
    try:
        job = q.get("stale-ask")
        assert job is not None and job.state.value == "failed"
        assert "restarted" in job.error
    finally:
        q.stop()
    assert calls == []                # never re-ran
    (row,) = _job_rows(ws)
    assert row["state"] == "failed" and "restarted" in row["error"]


def test_malformed_row_costs_one_job_not_the_server(tmp_path):
    """Round-3 review: the per-row try/except in the resume loops is the only
    thing between a hand-edited jobs row and `va serve` dying inside the
    FastAPI lifespan. Seed a bad row next to a good one: startup must succeed
    and the good job must complete."""
    clip = _clip(tmp_path)
    ws = tmp_path / ".va"

    store = JobStore(Workspace(str(ws)).catalog_db)
    store.record("bad-row", "ingest", {})                      # payload lacks uri
    store.record("good-row", "ingest", {"uri": str(clip), "fps": 1.0})
    store.record("bad-ask", "ask", {})                         # lacks question
    store.close()

    q = IngestQueue(str(ws))
    q.start()                                                  # must not raise
    try:
        assert _wait(lambda: (q.get("good-row") is not None
                              and q.get("good-row").state.value == "done"))
        bad = q.get("bad-row")                                 # pollable, failed
        assert bad is not None and bad.state.value == "failed"
        assert "unresumable" in bad.error
    finally:
        q.stop()

    aq = AskQueue(str(ws))
    aq.start()                                                 # must not raise
    aq.stop()

    # Terminal, not skip-forever: the malformed row must be FAILED in the
    # table, or every future boot re-warns about it for eternity.
    states = {r["id"]: r["state"] for r in _job_rows(ws)}
    assert states["bad-row"] == "failed"


def test_poison_job_gives_up_after_attempt_cap(tmp_path):
    """Round-4 review: a job that KILLS the process (OOM/segfault) can never
    persist its own failure — without a resume cap, systemd Restart=always
    becomes a crash loop resuming the same job forever."""
    from va.web.jobs import MAX_RESUME_ATTEMPTS

    clip = _clip(tmp_path)
    ws = tmp_path / ".va"
    store = JobStore(Workspace(str(ws)).catalog_db)
    store.record("poison", "ingest", {"uri": str(clip), "fps": 1.0})
    store.update("poison", "running")
    for _ in range(MAX_RESUME_ATTEMPTS):       # prior restarts already burned
        store.bump_attempts("poison")
    store.close()

    # An innocent job QUEUED behind the poison one through all those restarts:
    # it never ran, so it must not accrue attempts, and must complete now
    # (round-5 review: bumping queued rows failed innocents in lockstep).
    store = JobStore(Workspace(str(ws)).catalog_db)
    store.record("innocent", "ingest", {"uri": str(clip), "fps": 1.0})
    store.close()

    q = IngestQueue(str(ws))                   # one restart too many
    q.start()
    try:
        poison = q.get("poison")               # pollable, terminally failed
        assert poison is not None and poison.state.value == "failed"
        assert "gave up" in poison.error
        assert _wait(lambda: (q.get("innocent") is not None
                              and q.get("innocent").state.value == "done"))
    finally:
        q.stop()
    rows = {r["id"]: r for r in _job_rows(ws)}
    assert rows["poison"]["state"] == "failed"
    assert "gave up" in rows["poison"]["error"]
    assert rows["innocent"]["state"] == "done"
    assert rows["innocent"]["attempts"] == 0   # queued rows accrue no guilt


def test_corrupt_json_payload_blocks_no_one(tmp_path):
    """Round-6 review: one JSON-corrupt payload used to make pending() raise,
    silently blocking EVERY resume on every boot. It must cost one job."""
    clip = _clip(tmp_path)
    ws = tmp_path / ".va"
    store = JobStore(Workspace(str(ws)).catalog_db)
    store.record("healthy", "ingest", {"uri": str(clip), "fps": 1.0})
    store.close()
    con = sqlite3.connect(Workspace(str(ws)).catalog_db)
    con.execute("INSERT INTO jobs (id, kind, state, payload) "
                "VALUES ('corrupt', 'ingest', 'queued', '{not json')")
    con.commit()
    con.close()

    q = IngestQueue(str(ws))
    q.start()
    try:
        assert _wait(lambda: (q.get("healthy") is not None
                              and q.get("healthy").state.value == "done"))
    finally:
        q.stop()
    states = {r["id"]: r["state"] for r in _job_rows(ws)}
    assert states == {"healthy": "done", "corrupt": "failed"}


def test_graceful_stop_requeues_without_attempt_guilt(tmp_path, monkeypatch):
    """Round-6 review: a deliberate restart mid-job is NOT crash evidence — the
    in-flight job's row goes back to `queued`, keeping the poison cap for
    genuine kills only."""
    import threading

    import va.pipeline.ingest as ing_mod

    clip = _clip(tmp_path)
    ws = tmp_path / ".va"
    release = threading.Event()

    def blocking_ingest(uri, workdir, fps):
        release.wait(timeout=30)
        raise RuntimeError("unblocked late — result irrelevant")

    monkeypatch.setattr(ing_mod, "ingest", blocking_ingest)
    monkeypatch.setattr(IngestQueue, "JOIN_TIMEOUT", 0.2)

    q = IngestQueue(str(ws))
    q.start()
    job = q.submit(str(clip))
    assert _wait(lambda: _job_rows(ws)
                 and _job_rows(ws)[0]["state"] == "running")
    q.stop()                                   # graceful; join times out
    release.set()                              # let the daemon thread die

    (row,) = _job_rows(ws)
    assert row["state"] in ("queued", "failed")  # requeued (or the late
    # unblock's failed write raced in — both are terminal-visible states);
    # the essential assertion is the attempt count:
    assert row["attempts"] == 0                # no crash guilt accrued


def test_requeue_if_running_never_reverts_terminal_states(tmp_path):
    """Round-7/8 review: stop()'s requeue may race the worker's terminal write
    (join expired milliseconds early) — the guard must be a no-op on any
    non-running row, preserving done results."""
    ws = tmp_path / ".va"
    store = JobStore(Workspace(str(ws)).catalog_db)
    store.record("j", "ingest", {"uri": "x"})
    store.update("j", "done", result={"frames_indexed": 7})
    store.requeue_if_running("j")
    row = store.get("j")
    assert row["state"] == "done"
    assert row["result"] == {"frames_indexed": 7}
    # and the positive case: a running row DOES requeue
    store.update("j", "running")
    store.requeue_if_running("j")
    assert store.get("j")["state"] == "queued"
    store.close()


def test_resume_nulls_prior_appearance_refs_when_tracker_fails(tmp_path,
                                                               monkeypatch):
    """Round-7/8 review: the resume deletes appearance.npz, and prior-attempt
    track rows keep their refs if the tracker fails on the resumed run —
    those refs must be NULLED or they dangle into a nonexistent store,
    breaking the WS4.d invariant. The nulling lives in a swallow-all except,
    so only this test makes a regression visible."""
    import shutil as _shutil

    import yaml

    import va.pipeline.ingest as ing_mod
    from va.media.synth import write_box_video
    from va.pipeline.ingest import ingest

    # The stub detector needs a color vocab to produce tracks (the
    # appearance-store test rig): a red box under a "colors" profile.
    repo_config = Path(__file__).resolve().parents[1] / "config"
    cdir = tmp_path / "config"
    _shutil.copytree(repo_config, cdir)
    (cdir / "profiles" / "footage" / "colors.yaml").write_text(
        yaml.safe_dump({"roles": {"object_detector": {"classes": ["red"]}}}))
    monkeypatch.setenv("VA_CONFIG_DIR", str(cdir))
    clip = write_box_video(
        tmp_path / "clip.mp4", bg_rgb=(128, 128, 128), box_rgb=(220, 30, 30),
        box_frac=(0.25, 0.25, 0.5, 0.25), seconds=3.0, fps=10)
    ws = tmp_path / ".va"
    first = ingest(str(clip), workdir=str(ws), fps=1.0, profile="colors")
    assert first.tracks >= 1

    con = sqlite3.connect(Workspace(str(ws)).catalog_db)
    (refs_before,) = con.execute(
        "SELECT COUNT(*) FROM object_tracks WHERE appearance_ref IS NOT NULL"
    ).fetchone()
    assert refs_before >= 1
    con.execute("UPDATE videos SET ingest_status = 'processing'")  # mid-kill
    con.commit()
    con.close()

    def broken_tracker(cfg):
        raise RuntimeError("tracker OOM on the resumed run")

    monkeypatch.setattr(ing_mod, "get_object_tracker", broken_tracker)
    ingest(str(clip), workdir=str(ws), fps=1.0, profile="colors")  # the resume

    con = sqlite3.connect(Workspace(str(ws)).catalog_db)
    (dangling,) = con.execute(
        "SELECT COUNT(*) FROM object_tracks WHERE appearance_ref IS NOT NULL"
    ).fetchone()
    con.close()
    (video_dir,) = list((ws / "videos").iterdir())
    assert not (video_dir / "appearance.npz").exists()
    assert dangling == 0             # no refs into the deleted store


def test_no_workdir_store_failure_degrades_to_memory(tmp_path, monkeypatch):
    """Durability must never kill the queue: a broken jobs table degrades to
    the pre-WS6.a memory-only behavior with a warning."""
    clip = _clip(tmp_path)
    ws = tmp_path / ".va"

    import va.web.jobs as jobs_mod

    def broken_store(self):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(jobs_mod.SerialQueue, "_store", broken_store)
    q = IngestQueue(str(ws))
    q.start()
    try:
        job = q.submit(str(clip))
        assert _wait(lambda: job.state.value == "done")
    finally:
        q.stop()
