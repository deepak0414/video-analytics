"""WS3.b — start_epoch (schema v5) + wall-clock↔relative translation.

Done-conditions from architecture-evolution-loop.md: unit tests translate a
wall-clock range to (chunk, relative-range) sets across multiple chunks,
including gaps and NULL-epoch videos.
"""
import sqlite3

from va.contracts.video import SourceType, Video
from va.media.synth import write_color_video
from va.pipeline.ingest import ingest
from va.pipeline.timeline import absolute_time, wallclock_to_chunks
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.structured.schema import SCHEMA_VERSION, connect

BASE = 1_775_000_000.0  # an arbitrary UTC epoch anchor


def _chunk(key: str, start: float | None, duration: float | None = 600.0) -> Video:
    return Video(source_type=SourceType.local, source_uri=f"/{key}", source_key=key,
                 start_epoch=start, duration_seconds=duration)


def test_translation_across_chunks_with_gap_and_null():
    # Two 10-min chunks with a 5-min gap between them + one relative-only video.
    a = _chunk("a", BASE)                    # [BASE, BASE+600]
    b = _chunk("b", BASE + 900)              # [BASE+900, BASE+1500]
    aev = _chunk("aev", None)                # relative-only: must be skipped

    # A range spanning the tail of a, the whole gap, and the head of b.
    got = wallclock_to_chunks([b, aev, a], BASE + 500, BASE + 1000)
    assert [(c.video_id, c.rel_start, c.rel_end) for c in got] == [
        (str(a.id), 500.0, 600.0),   # last 100s of chunk a
        (str(b.id), 0.0, 100.0),     # first 100s of chunk b
    ]
    # Entirely inside the gap -> nothing.
    assert wallclock_to_chunks([a, b], BASE + 700, BASE + 800) == []


def test_translation_clamps_and_orders():
    a = _chunk("a", BASE)
    inside = wallclock_to_chunks([a], BASE + 60, BASE + 120)[0]
    assert (inside.rel_start, inside.rel_end) == (60.0, 120.0)
    covering = wallclock_to_chunks([a], BASE - 100, BASE + 10_000)[0]
    assert (covering.rel_start, covering.rel_end) == (0.0, 600.0)


def test_unknown_duration_chunk_is_capped_at_the_range_end():
    unknown = _chunk("u", BASE, duration=None)
    got = wallclock_to_chunks([unknown], BASE + 50, BASE + 80)
    assert [(c.rel_start, c.rel_end) for c in got] == [(50.0, 80.0)]
    # Starts after the range end -> excluded even with unknown duration.
    assert wallclock_to_chunks([_chunk("u2", BASE + 500, None)], BASE, BASE + 100) == []


def test_reingest_preserves_start_epoch(tmp_path):
    from va.pipeline.manage import reingest_video

    clip = write_color_video(tmp_path / "c.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    wd = str(tmp_path / ".va")
    res = ingest(str(clip), workdir=wd, fps=1.0)
    cat = Catalog(tmp_path / ".va" / "catalog.db")
    cat.set_start_epoch(res.video.id, BASE)
    cat.close()

    again = reingest_video(wd, str(clip), fps=1.0)
    assert again.video.start_epoch == BASE
    cat = Catalog(tmp_path / ".va" / "catalog.db")
    try:
        assert cat.get(again.video.id).start_epoch == BASE
    finally:
        cat.close()


def test_absolute_time_roundtrip_and_null():
    v = _chunk("a", BASE)
    assert absolute_time(v, 42.5) == BASE + 42.5
    assert absolute_time(_chunk("aev", None), 42.5) is None


def test_schema_v5_and_null_for_existing_rows(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION >= 5
        assert any(r[1] == "start_epoch"
                   for r in conn.execute("PRAGMA table_info(videos)"))
    finally:
        conn.close()

    # v4-era DB with a row migrates in place; the row reads back start_epoch=None.
    db = tmp_path / "old.db"
    connect(db).close()
    raw = sqlite3.connect(db)
    raw.execute("ALTER TABLE videos DROP COLUMN start_epoch")
    raw.execute("PRAGMA user_version = 4")
    raw.execute(
        "INSERT INTO videos (id, source_type, source_uri, source_key, ingest_status,"
        " created_at) VALUES ('00000000-0000-0000-0000-000000000001', 'local', '/x',"
        " 'k1', 'done', '2026-01-01T00:00:00+00:00')")
    raw.commit()
    raw.close()
    cat = Catalog(db)
    try:
        assert cat.get_by_source_key("k1").start_epoch is None
    finally:
        cat.close()


def test_aev_ingest_keeps_start_epoch_null_and_setter_works(tmp_path):
    clip = write_color_video(tmp_path / "c.mp4", [("red", (220, 30, 30), 2.0)], fps=10)
    res = ingest(str(clip), workdir=str(tmp_path / ".va"), fps=1.0)
    assert res.video.start_epoch is None
    cat = Catalog(tmp_path / ".va" / "catalog.db")
    try:
        cat.set_start_epoch(res.video.id, BASE)
        assert cat.get(res.video.id).start_epoch == BASE
    finally:
        cat.close()
