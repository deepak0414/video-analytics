"""Schema migration runner: versioned `PRAGMA user_version` migrations that
bring an older workdir's catalog DB forward on open, without disturbing existing
rows. Foundation for evolving the schema (profiles, cameras, wall-clock time, …)
against corpora that are already ingested.
"""
import logging
import sqlite3

import pytest

from va.storage.structured import schema as S
from va.storage.structured.schema import (
    MIGRATIONS,
    SCHEMA_VERSION,
    add_column,
    connect,
)

# The `videos` schema as it was BEFORE the v1 migration (no last_ingest_run_id) —
# a realistic stand-in for a DB ingested by an older build, so the migration tests
# exercise the real upgrade path rather than a toy table.
_V0_VIDEOS = """
CREATE TABLE videos (
    id TEXT PRIMARY KEY NOT NULL, source_type TEXT NOT NULL, source_uri TEXT NOT NULL,
    source_key TEXT NOT NULL UNIQUE, local_path TEXT, title TEXT, duration_seconds REAL,
    fps REAL, resolution TEXT, has_audio INTEGER,
    ingest_status TEXT NOT NULL DEFAULT 'pending', ingest_error TEXT,
    created_at TEXT, fetched_at TEXT, processed_at TEXT
);
"""


def _uv(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _make_v0_db(path):
    raw = sqlite3.connect(path)
    raw.executescript(_V0_VIDEOS)
    raw.execute("PRAGMA user_version = 0")
    raw.commit()
    raw.close()


def test_migration_count_matches_version():
    assert len(MIGRATIONS) == SCHEMA_VERSION


def test_fresh_db_is_stamped_current_version(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    try:
        assert _uv(conn) == SCHEMA_VERSION
        assert "last_ingest_run_id" in _cols(conn, "videos")   # v1 column present
    finally:
        conn.close()


def test_old_v0_db_migrates_forward_preserving_rows(tmp_path):
    p = tmp_path / "old.db"
    _make_v0_db(p)
    raw = sqlite3.connect(p)
    raw.execute("INSERT INTO videos (id, source_type, source_uri, source_key) "
                "VALUES ('v1','youtube','http://x','k1')")
    raw.commit()
    raw.close()

    conn = connect(p)                       # apply_schema runs on open
    try:
        assert _uv(conn) == SCHEMA_VERSION                       # advanced
        assert "last_ingest_run_id" in _cols(conn, "videos")     # column backfilled
        # the existing row survives; the new column is NULL for it
        row = conn.execute(
            "SELECT id, last_ingest_run_id FROM videos WHERE id='v1'"
        ).fetchone()
        assert row[0] == "v1" and row[1] is None
    finally:
        conn.close()


def test_fresh_and_migrated_schemas_agree(tmp_path):
    """Guards base-DDL / migration drift: a fresh DB and a DB upgraded from the
    pre-v1 schema must end with identical `videos` columns. If someone adds a column
    to the base DDL but forgets the matching migration (or vice-versa), this fails."""
    fresh = connect(tmp_path / "fresh.db")
    fresh_cols = _cols(fresh, "videos")
    fresh.close()

    p = tmp_path / "old.db"
    _make_v0_db(p)
    migrated = connect(p)
    migrated_cols = _cols(migrated, "videos")
    migrated.close()

    assert migrated_cols == fresh_cols


def test_reopen_is_a_noop_fast_path(tmp_path):
    p = tmp_path / "idem.db"
    c1 = connect(p); v1 = _uv(c1); c1.close()
    c2 = connect(p); v2 = _uv(c2); c2.close()   # second open hits the already-current fast path
    assert v1 == v2 == SCHEMA_VERSION


def test_add_column_is_idempotent(tmp_path):
    conn = connect(tmp_path / "c.db")
    try:
        add_column(conn, "videos", "last_ingest_run_id", "TEXT")   # already exists -> no-op
        add_column(conn, "videos", "scratch_col", "TEXT")          # new -> added
        add_column(conn, "videos", "scratch_col", "TEXT")          # again -> no-op, no error
        assert "scratch_col" in _cols(conn, "videos")
    finally:
        conn.close()


def test_failed_migration_rolls_back_atomically(tmp_path, monkeypatch):
    """A migration that changes the schema then fails must leave NOTHING behind —
    version un-advanced, partial DDL rolled back — thanks to the per-migration
    BEGIN IMMEDIATE transaction."""
    def _bad(conn):
        add_column(conn, "videos", "half_done", "TEXT")   # partial change...
        raise RuntimeError("boom")                        # ...then fail

    monkeypatch.setattr(S, "MIGRATIONS", list(S.MIGRATIONS) + [_bad])
    monkeypatch.setattr(S, "SCHEMA_VERSION", len(S.MIGRATIONS))   # == new count

    p = tmp_path / "x.db"
    with pytest.raises(RuntimeError, match="boom"):
        connect(p)                          # migrates 0->1 (ok), then the bad 1->2 fails

    raw = sqlite3.connect(p)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 1   # v1 committed
        cols = {r[1] for r in raw.execute("PRAGMA table_info(videos)")}
        assert "last_ingest_run_id" in cols     # v1 landed and stuck
        assert "half_done" not in cols          # the failed v2 rolled back
    finally:
        raw.close()


def test_newer_db_than_code_warns_and_does_not_downgrade(tmp_path, caplog):
    """A DB written by a NEWER build (user_version > SCHEMA_VERSION) is left
    untouched — no downgrade — with a warning. This is the open-a-newer-workdir
    behavior COORDINATION.md promises the other agents."""
    p = tmp_path / "newer.db"
    conn = connect(p)                                            # create at current version
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")  # pretend a newer build wrote it
    conn.commit()
    conn.close()

    with caplog.at_level(logging.WARNING, logger="va.storage.structured.schema"):
        conn = connect(p)                                        # reopen with THIS (older) build
    try:
        assert _uv(conn) == SCHEMA_VERSION + 1                   # version NOT downgraded
        assert "newer version" in caplog.text                    # and it warned
    finally:
        conn.close()
