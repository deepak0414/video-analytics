"""`va stale` / stale_report (WS-1 §6-b, PROV-4): compare each video's recorded
provenance fingerprint against the current config's. Offline: stub ingest, then poke
the recorded provenance to simulate a model change.
"""
import sqlite3

from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.paths import Workspace
from va.pipeline.stale import stale_report
from va.provenance import PROVENANCE_ROLES
from va.storage.structured.provenance_store import ProvenanceStore


def _clip(tmp_path):
    return write_color_video(
        tmp_path / "clip.mp4",
        [("red", (220, 30, 30), 2.0), ("green", (30, 180, 30), 2.0)], fps=10)


def test_nothing_stale_right_after_ingest(tmp_path):
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    assert stale_report(wd) == []                     # recorded == current for every role


def test_a_role_with_a_changed_fingerprint_is_stale(tmp_path):
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    # simulate: this video's OCR was produced by an OLD model (different fingerprint)
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        pv.record(res.video.id, "ocr", "old-model", "STALE-FP")
    finally:
        pv.close()

    report = stale_report(wd)
    assert len(report) == 1
    assert report[0]["stale_roles"] == ["ocr"]        # only ocr differs from current


def test_video_without_provenance_is_fully_stale(tmp_path):
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    db = Workspace(wd).catalog_db
    conn = sqlite3.connect(db)                        # wipe it -> a pre-PROV-3-style video
    conn.execute("DELETE FROM role_provenance WHERE video_id=?", (str(res.video.id),))
    conn.commit()
    conn.close()

    report = stale_report(wd)
    assert len(report) == 1
    assert set(report[0]["stale_roles"]) == set(PROVENANCE_ROLES)   # unknown -> stale everywhere


def test_role_filter_scopes_the_check(tmp_path):
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        pv.record(res.video.id, "ocr", "old-model", "STALE-FP")   # only ocr is stale
    finally:
        pv.close()

    assert stale_report(wd, role="visual_embedder") == []          # a current role: clean
    assert stale_report(wd, role="ocr")[0]["stale_roles"] == ["ocr"]


def test_report_surfaces_recorded_fps(tmp_path):
    # fps is a run arg with no config baseline, so it's REPORTED not compared — but the
    # report must surface it, because `va reingest` defaults to fps=1.0 and a reprocess
    # needs the original fps to preserve the frame density Roles 2/5/6/7 saw.
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=2.0)
    pv = ProvenanceStore(Workspace(wd).catalog_db)
    try:
        pv.record(res.video.id, "ocr", "old-model", "STALE-FP", fps=2.0)   # stale, same fps
    finally:
        pv.close()

    report = stale_report(wd)
    assert len(report) == 1
    assert report[0]["stale_roles"] == ["ocr"]
    assert report[0]["recorded_fps"] == 2.0            # the ingest fps, surfaced for reprocess


def test_non_done_video_is_skipped(tmp_path):
    # a never-completed ingest has no rows to reprocess (it needs re-ingest, not a role
    # reprocess), so it must NOT appear in `va stale` even with no provenance at all —
    # otherwise it reads as stale-everywhere, conflating incomplete-ingest with drift.
    from va.contracts.video import IngestStatus
    from va.storage.structured.catalog_sqlite import Catalog

    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    db = Workspace(wd).catalog_db
    conn = sqlite3.connect(db)                        # wipe provenance: stale-everywhere IF considered
    conn.execute("DELETE FROM role_provenance WHERE video_id=?", (str(res.video.id),))
    conn.commit()
    conn.close()
    cat = Catalog(db)
    try:
        cat.set_status(res.video.id, IngestStatus.fetching)   # flip out of `done`
    finally:
        cat.close()

    assert stale_report(wd) == []                    # skipped despite missing provenance


def test_unknown_role_raises_rather_than_reporting_all_stale(tmp_path):
    # an unstamped role (reasoner) or a typo would match no recorded row and mark EVERY
    # video stale — a confidently-wrong report. stale_report must reject it, not guess.
    import pytest

    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    for bad in ("reasoner", "speech_to_txt"):
        with pytest.raises(ValueError):
            stale_report(wd, role=bad)
