"""Ingest<->query trace linking: an ingest stamps its trace run_id onto the
video row, and a later query/ask trace names the ingest run(s) behind its hits.

Offline: stub embedder + synth color clips, tracing forced on via VA_TRACE / an
explicit `traced_run(enabled=True)` (no env dependence for the query side).
"""
import sqlite3

from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.paths import Workspace
from va.pipeline.query import query
from va.runtime.trace import list_runs, load_events
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.schema import connect


def _clip(tmp_path):
    return write_color_video(
        tmp_path / "clip.mp4",
        [("red", (220, 30, 30), 3.0), ("green", (30, 180, 30), 3.0)],
        fps=10,
    )


def _catalog_video(wd, video_id):
    cat = Catalog(Workspace(wd).catalog_db)
    try:
        return cat.get(video_id)
    finally:
        cat.close()


def test_traced_ingest_stamps_run_id_on_video(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_TRACE", "1")
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)

    v = _catalog_video(wd, res.video.id)
    ingest_run = [r for r in list_runs(wd) if r["kind"] == "ingest"][0]["run_id"]
    assert v.last_ingest_run_id == ingest_run   # points at THIS ingest's trace


def test_untraced_ingest_leaves_run_id_none(tmp_path, monkeypatch):
    monkeypatch.delenv("VA_TRACE", raising=False)
    wd = str(tmp_path / ".va")
    res = ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    assert _catalog_video(wd, res.video.id).last_ingest_run_id is None


def _run_query_cli(text, wd):
    import argparse

    from va.cli import _cmd_query
    _cmd_query(argparse.Namespace(text=text, workdir=wd, k=5, verify=False))


def _link_event(wd, kind):
    path = [r for r in list_runs(wd) if r["kind"] == kind][0]["path"]
    return next(e for e in load_events(path)
                if (e["role"], e["action"]) == ("link", "ingest_runs"))


def test_query_trace_names_the_ingest_run(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_TRACE", "1")
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    ingest_run = [r for r in list_runs(wd) if r["kind"] == "ingest"][0]["run_id"]

    _run_query_cli("red", wd)
    assert ingest_run in _link_event(wd, "query")["details"].values()   # jumpable back


def test_query_link_is_null_when_ingest_was_untraced(tmp_path, monkeypatch):
    # ingest with tracing OFF -> row has no run_id; the query link degrades to null
    monkeypatch.delenv("VA_TRACE", raising=False)
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)

    monkeypatch.setenv("VA_TRACE", "1")                 # now trace the query
    _run_query_cli("red", wd)
    assert None in _link_event(wd, "query")["details"].values()


def test_ask_emits_exactly_one_link(tmp_path, monkeypatch):
    # guard against the double-emit: ask()'s retrieve() calls query() internally,
    # so the link must live at the entry point, not inside query().
    monkeypatch.setenv("VA_TRACE", "1")
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)

    from va.pipeline.ask import ask
    ask("what color is shown?", workdir=wd, k=5)

    apath = [r for r in list_runs(wd) if r["kind"] == "ask"][0]["path"]
    links = [e for e in load_events(apath)
             if (e["role"], e["action"]) == ("link", "ingest_runs")]
    assert len(links) == 1


def test_bare_query_call_is_not_a_trace_owner(tmp_path, monkeypatch):
    # query() is a reusable building block (retrieve() calls it) — on its own it
    # opens no traced_run, so no query trace and no link even under VA_TRACE=1.
    monkeypatch.setenv("VA_TRACE", "1")
    wd = str(tmp_path / ".va")
    ingest(str(_clip(tmp_path)), workdir=wd, fps=1.0)
    assert query("red", workdir=wd, k=5)
    assert not [r for r in list_runs(wd) if r["kind"] == "query"]


def test_schema_migration_backfills_column_on_old_db(tmp_path):
    # simulate a DB created before the column existed: a `videos` table without it
    p = tmp_path / "old.db"
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE videos (id TEXT PRIMARY KEY, source_key TEXT)")
    raw.commit()
    raw.close()

    conn = connect(p)   # apply_schema sees videos exists -> ALTERs in the new column
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
    finally:
        conn.close()
    assert "last_ingest_run_id" in cols
