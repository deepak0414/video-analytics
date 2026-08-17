"""Role 5/6 in-place reprocess (`va reprocess --role object_detector`). Re-running the detector
also rebuilds the tracks in one pass, so `_SATISFIES` restamps object_tracker rather than
re-running it. Covers the .va-24h gap where the detector was NEVER stamped: the fps must fall
back to a role that did run (the visual embedder shares the ingest density). Offline: the color
stub detector "detects" colors, so the ingest vocabulary is set to the box color.
"""
import shutil
import sqlite3
from pathlib import Path

from va.media.synth import write_box_video
from va.pipeline.ingest import ingest
from va.pipeline.paths import Workspace
from va.pipeline.reprocess import execute_reprocess, plan_reprocess
from va.storage.structured.provenance_store import ProvenanceStore

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _ingest_with_box(tmp_path, monkeypatch):
    # stub detector matches colors -> make the vocabulary the box color, for BOTH the ingest
    # binding and the registry binding the reprocessor imports fresh inside the function.
    import va.pipeline.ingest as ingest_mod
    import va.registry as registry_mod
    monkeypatch.setattr(ingest_mod, "get_ingest_classes", lambda *a, **k: ["red", "blue"])
    monkeypatch.setattr(registry_mod, "get_ingest_classes", lambda *a, **k: ["red", "blue"])

    video = write_box_video(
        tmp_path / "clip.mp4", bg_rgb=(128, 128, 128), box_rgb=(220, 30, 30),
        box_frac=(0.25, 0.25, 0.5, 0.25), seconds=3.0, fps=10,
    )
    wd = str(tmp_path / ".va")
    res = ingest(str(video), workdir=wd, fps=1.0)
    assert res.detections >= 3 and res.tracks >= 1   # ingest produced Role 5 + 6 rows
    return wd, str(res.video.id)


def _detection_count(wd, vid):
    con = sqlite3.connect(Workspace(wd).catalog_db)
    try:
        return con.execute(
            "select count(*) from object_detections where video_id=?", (vid,)).fetchone()[0]
    finally:
        con.close()


def _track_count(wd, vid):
    con = sqlite3.connect(Workspace(wd).catalog_db)
    try:
        return con.execute(
            "select count(*) from object_tracks where video_id=?", (vid,)).fetchone()[0]
    finally:
        con.close()


def _make_stale(wd, vid, role):
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        pv.record(vid, role, "old-model", "STALE-FP", fps=1.0)   # != current fingerprint
    finally:
        pv.close()


def test_reprocess_object_detector_rebuilds_tracks_and_clears_both(tmp_path, monkeypatch):
    wd, vid = _ingest_with_box(tmp_path, monkeypatch)
    _make_stale(wd, vid, "object_detector")
    _make_stale(wd, vid, "object_tracker")

    plan = plan_reprocess(wd, all_stale=True)
    assert set(plan[0]["stale_roles"]) >= {"object_detector", "object_tracker"}

    result = execute_reprocess(wd, plan)
    done = {r for _, r, _ in result["reprocessed"]}
    # detector re-ran in place; tracker was restamped via the _SATISFIES dependency, not re-run.
    assert {"object_detector", "object_tracker"} <= done
    assert not result["failed"]
    assert _detection_count(wd, vid) >= 3
    # both roles are current afterward (fingerprint restamped)
    assert plan_reprocess(wd, all_stale=True, role="object_detector") == []
    assert plan_reprocess(wd, all_stale=True, role="object_tracker") == []


def test_reprocess_object_detector_refuses_zero_frames(tmp_path, monkeypatch):
    """A video that HAD frames at ingest but now decodes ZERO (truncated/corrupt media) must NOT
    wipe its detections/tracks. The reprocess must raise before any write — leaving the prior rows
    intact and the role stale for retry — mirroring reindex_visual's 0-frame refusal. Otherwise
    `va stale` reads clean while `va count` answers nothing forever."""
    wd, vid = _ingest_with_box(tmp_path, monkeypatch)
    before_dets, before_tracks = _detection_count(wd, vid), _track_count(wd, vid)
    assert before_dets >= 3 and before_tracks >= 1

    _make_stale(wd, vid, "object_detector")
    # Media now decodes nothing (function-local import re-reads the module attr, so patching the
    # source module reaches the reprocessor).
    import va.media.frames as frames_mod
    monkeypatch.setattr(frames_mod, "sample_frames", lambda *a, **k: iter(()))

    result = execute_reprocess(wd, plan_reprocess(wd, all_stale=True))
    assert any(r == "object_detector" for _, r, _ in result["failed"])  # raised, did not wipe
    assert not result["reprocessed"]                                     # nothing restamped
    assert _detection_count(wd, vid) == before_dets                      # prior rows survived
    assert _track_count(wd, vid) == before_tracks
    assert plan_reprocess(wd, all_stale=True, role="object_detector") != []  # still stale


def test_reprocess_object_detector_aborts_if_video_removed_midway(tmp_path, monkeypatch):
    """A `va remove` landing DURING the (long) re-detect — after the initial lookup, before the
    writes — must abort without re-inserting rows or restamping, matching reindex_visual's guard.
    Otherwise a removed video's detections/tracks get resurrected by the replace_* writes."""
    wd, vid = _ingest_with_box(tmp_path, monkeypatch)

    import va.registry as registry_mod
    from va.pipeline.manage import remove_video
    real_get_tracker = registry_mod.get_object_tracker  # reprocess imports this function-locally

    class _RemoveThenTrack:
        # Simulate the concurrent remove at the mid-run point: right when tracking would run,
        # after frames are decoded (so the 0-frame guard passes) but before the writes.
        def __init__(self, inner):
            self._inner = inner

        def track(self, video_id, frames_dets):
            remove_video(wd, vid)
            return self._inner.track(video_id, frames_dets)

    monkeypatch.setattr(registry_mod, "get_object_tracker",
                        lambda cfg: _RemoveThenTrack(real_get_tracker(cfg)))

    _make_stale(wd, vid, "object_detector")
    result = execute_reprocess(wd, plan_reprocess(wd, all_stale=True))
    assert any(r == "object_detector" for _, r, _ in result["failed"])   # aborted, not restamped
    assert not result["reprocessed"]
    # the removed video's rows were NOT resurrected by the reprocess writes
    assert _detection_count(wd, vid) == 0 and _track_count(wd, vid) == 0


def test_reprocess_object_detector_when_never_stamped_uses_ingest_fps(tmp_path, monkeypatch):
    # The real .va-24h gap: the detector/tracker were never stamped (no rows at all). Reprocess
    # must still resolve the density from the visual embedder's recorded fps, not refuse.
    wd, vid = _ingest_with_box(tmp_path, monkeypatch)
    con = sqlite3.connect(Workspace(wd).catalog_db)
    con.execute("delete from role_provenance where video_id=? "
                "and role in ('object_detector','object_tracker')", (vid,))
    con.execute("delete from object_detections where video_id=?", (vid,))
    con.execute("delete from object_tracks where video_id=?", (vid,))
    con.commit()
    con.close()

    plan = plan_reprocess(wd, all_stale=True)
    result = execute_reprocess(wd, plan)
    done = {r for _, r, _ in result["reprocessed"]}
    assert {"object_detector", "object_tracker"} <= done
    assert not result["failed"]        # did NOT refuse for unknown fps
    assert _detection_count(wd, vid) >= 3
    assert plan_reprocess(wd, all_stale=True, role="object_detector") == []


def test_reprocess_object_detector_honors_disabled_tracker(tmp_path, monkeypatch):
    """A footage profile may disable object_tracker while KEEPING the detector (both are
    independently gateable). Reprocessing the stale detector must then store UNTRACKED detections
    and write ZERO tracks — not regenerate the tracks the profile forbids (which `va stale` would
    exclude forever and `va count` would answer from). Exercises the REAL config_for/load_config
    overlay the reprocessor reads. Fails on a reprocessor that calls the tracker unconditionally."""
    # A temp config dir (full copy of the repo config) plus a profile that disables the tracker
    # and narrows the vocab to the box color (so the color stub still "detects" it).
    cfg_dir = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, cfg_dir)
    (cfg_dir / "profiles" / "footage" / "trackeroff.yaml").write_text(
        "roles:\n"
        "  object_detector:\n"
        "    classes: [red, blue]\n"
        "  object_tracker:\n"
        "    enabled: false\n"
    )
    monkeypatch.setenv("VA_CONFIG_DIR", str(cfg_dir))

    video = write_box_video(
        tmp_path / "clip.mp4", bg_rgb=(128, 128, 128), box_rgb=(220, 30, 30),
        box_frac=(0.25, 0.25, 0.5, 0.25), seconds=3.0, fps=10,
    )
    wd = str(tmp_path / ".va")
    res = ingest(str(video), workdir=wd, fps=1.0, profile="trackeroff")
    vid = str(res.video.id)
    # Ingest under the profile already gates the tracker: detections stored untracked, zero tracks.
    assert _detection_count(wd, vid) >= 3
    assert _track_count(wd, vid) == 0

    _make_stale(wd, vid, "object_detector")
    plan = plan_reprocess(wd, all_stale=True)
    result = execute_reprocess(wd, plan)

    done = {r for _, r, _ in result["reprocessed"]}
    assert "object_detector" in done          # detector re-ran
    assert "object_tracker" not in done        # disabled -> never re-run or restamped
    assert not result["failed"]
    assert _detection_count(wd, vid) >= 3       # detections rebuilt
    assert _track_count(wd, vid) == 0           # tracker stayed OFF (old code would write tracks)
