"""Central correlation DB schema (SQLite for the PoC; Postgres later).

One structured store holds `videos` + one table per role's metadata, every row
keyed by `video_id` (and usually a `timestamp` or `segment_id`). Complex queries
correlate roles via temporal SQL joins on these keys, e.g.:

    SELECT * FROM object_detections d
    JOIN action_events a
      ON a.video_id = d.video_id
     AND a.start_time <= d.timestamp AND d.timestamp <= a.end_time
    WHERE d.object_class = 'squirrel' AND a.action_class = 'eating';

All tables are created up front (cheap) and populated as each role is implemented.
Today only `videos` (Role 0/catalog) and `segments` (Role 1) are written to.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Role 0: video catalog (the registry; universal join key videos.id) -------
VIDEOS = """
CREATE TABLE IF NOT EXISTS videos (
    id            TEXT PRIMARY KEY,
    source_type   TEXT NOT NULL,
    source_uri    TEXT NOT NULL,
    source_key    TEXT NOT NULL UNIQUE,
    local_path    TEXT,
    title         TEXT,
    duration_seconds REAL,
    fps           REAL,
    resolution    TEXT,
    has_audio     INTEGER,
    ingest_status TEXT NOT NULL DEFAULT 'pending',
    ingest_error  TEXT,
    created_at    TEXT,
    fetched_at    TEXT,
    processed_at  TEXT,
    last_ingest_run_id TEXT   -- trace run_id of the ingest that last wrote this row
                              -- (ingest<->query trace link); NULL when ingested untraced
);
"""

# --- Role 1: scene boundaries (the temporal backbone) -------------------------
SEGMENTS = """
CREATE TABLE IF NOT EXISTS segments (
    id            TEXT PRIMARY KEY NOT NULL,  -- SQLite TEXT PK permits NULL without this!
    video_id      TEXT NOT NULL REFERENCES videos(id),
    segment_index INTEGER NOT NULL,
    start_time    REAL NOT NULL,
    end_time      REAL NOT NULL,
    keyframe_paths TEXT,            -- JSON array (Role 1 keyframe selection)
    caption       TEXT,            -- filled by Role 4 (VLM captioner)
    UNIQUE(video_id, segment_index)
);
"""

# --- Role 5/6: object detection + tracking ------------------------------------
OBJECT_TRACKS = """
CREATE TABLE IF NOT EXISTS object_tracks (
    id            TEXT PRIMARY KEY,
    video_id      TEXT NOT NULL REFERENCES videos(id),
    object_class  TEXT NOT NULL,
    track_confidence REAL,
    first_seen    REAL,
    last_seen     REAL,
    frame_count   INTEGER
);
"""

OBJECT_DETECTIONS = """
CREATE TABLE IF NOT EXISTS object_detections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL REFERENCES videos(id),
    timestamp     REAL NOT NULL,
    track_id      TEXT REFERENCES object_tracks(id),
    object_class  TEXT NOT NULL,
    bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
    confidence    REAL
);
"""

# --- Role 7: actions/events ---------------------------------------------------
ACTION_EVENTS = """
CREATE TABLE IF NOT EXISTS action_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL REFERENCES videos(id),
    segment_id    TEXT REFERENCES segments(id),
    action_class  TEXT NOT NULL,
    confidence    REAL,
    start_time    REAL,
    end_time      REAL
);
"""

# --- Role 8/9: transcripts (+ speaker) ----------------------------------------
TRANSCRIPTS = """
CREATE TABLE IF NOT EXISTS transcripts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL REFERENCES videos(id),
    start_time    REAL,
    end_time      REAL,
    speaker       TEXT,
    text          TEXT NOT NULL
);
"""

# --- Role 10: on-screen text --------------------------------------------------
OCR_RESULTS = """
CREATE TABLE IF NOT EXISTS ocr_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL REFERENCES videos(id),
    timestamp     REAL,
    text          TEXT NOT NULL,
    bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL
);
"""

# --- Tier 5b: deep-scan observation cache --------------------------------------
# Per-frame micro-captions produced by query-time exhaustive sweeps. Keyed by
# (video_id, prompt_key) so a repeat question reuses the sweep for free.
OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL REFERENCES videos(id),
    prompt_key    TEXT NOT NULL,
    timestamp     REAL NOT NULL,
    text          TEXT NOT NULL
);
"""

# §6-b: per-(video, role) provenance — the model/config fingerprint that produced each
# role's rows (see va.provenance.role_fingerprint), plus run args not in (role, cfg) like
# `fps`. Read by `va stale` (PROV-4) and the selective reprocess (B).
ROLE_PROVENANCE = """
CREATE TABLE IF NOT EXISTS role_provenance (
    video_id     TEXT NOT NULL REFERENCES videos(id),
    role         TEXT NOT NULL,
    model        TEXT,
    fingerprint  TEXT NOT NULL,
    fps          REAL,
    run_id       TEXT,
    row_count    INTEGER,
    produced_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (video_id, role)
);
"""

# Index video_id on the high-volume per-frame tables for correlation joins.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_segments_video ON segments(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_detections_video_ts ON object_detections(video_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_actions_video ON action_events(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_transcripts_video ON transcripts(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_ocr_video ON ocr_results(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_observations_key ON observations(video_id, prompt_key, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_provenance_role ON role_provenance(role)",
]

ALL_TABLES = [
    VIDEOS, SEGMENTS, OBJECT_TRACKS, OBJECT_DETECTIONS,
    ACTION_EVENTS, TRANSCRIPTS, OCR_RESULTS, OBSERVATIONS, ROLE_PROVENANCE,
]

# --- schema versioning + migrations -------------------------------------------
# The DB records its schema version in SQLite's `PRAGMA user_version`. A fresh DB
# is created at the current schema (ALL_TABLES); an older DB is brought forward by
# running the ordered MIGRATIONS whose target exceeds its current version, each
# advancing user_version. This generalizes the earlier one-off column-ensure into a
# real migration path, so we can add columns, add tables, and backfill data as the
# product evolves WITHOUT breaking already-ingested corpora — every workdir's DB
# migrates itself the next time a store opens it.
#
# To make a schema change: (1) reflect it in the base DDL above so fresh DBs get it
# directly, (2) append an *idempotent* migration function below, (3) bump
# SCHEMA_VERSION. Migrations also run on fresh DBs (as cheap no-ops), so keeping
# them idempotent lets one code path serve both fresh and upgrading DBs. An index on
# a migrated-in column is safe: apply_schema() builds INDEXES *after* running the
# migrations, so the column already exists by the time its index is created.

SCHEMA_VERSION = 2


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Idempotent `ALTER TABLE … ADD COLUMN` (SQLite has no IF NOT EXISTS for it).
    Additive, nullable columns only — safe to re-run and safe on a DB an earlier
    path already touched. The building block for column-add migrations."""
    if not _has_column(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _m1_last_ingest_run_id(conn: sqlite3.Connection) -> None:
    """→ v1: the ingest<->query trace-link column (formerly `_ensure_videos_columns`).
    Idempotent so pre-versioning DBs that already got it via that path convert cleanly."""
    add_column(conn, "videos", "last_ingest_run_id", "TEXT")


def _m2_role_provenance(conn: sqlite3.Connection) -> None:
    """→ v2: the per-(video, role) provenance table (WS-1 §6-b). CREATE IF NOT EXISTS is
    idempotent and a no-op on a fresh DB (ALL_TABLES already made it)."""
    conn.execute(ROLE_PROVENANCE)


# Ordered; MIGRATIONS[i] takes the DB from version i to version i+1.
MIGRATIONS = [
    _m1_last_ingest_run_id,          # -> 1
    _m2_role_provenance,             # -> 2
]
assert len(MIGRATIONS) == SCHEMA_VERSION, "SCHEMA_VERSION must equal the migration count"


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply each pending migration in its OWN transaction. `BEGIN IMMEDIATE` takes
    the write lock before the migration runs, which buys two things: (a) the
    migration and its version stamp commit **atomically** — a crash mid-migration
    rolls back instead of leaving half-applied DDL with the version un-advanced;
    (b) two processes opening the same not-yet-migrated DB **serialize** on the lock;
    the loser re-reads user_version inside its transaction and skips any migration the
    winner already applied, so it never re-runs a migration or stamps the version
    backward (`add_column`'s idempotence covers the rest).
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for target in range(current + 1, SCHEMA_VERSION + 1):
        conn.commit()                       # ensure no implicit txn is open before BEGIN
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Re-read under the write lock: a process that raced us here may have
            # already advanced past `target`. Skip rather than re-run and risk
            # stamping the version backward.
            if conn.execute("PRAGMA user_version").fetchone()[0] >= target:
                conn.rollback()
                continue
            MIGRATIONS[target - 1](conn)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def apply_schema(conn: sqlite3.Connection) -> None:
    """Ensure the DB exists at the current schema version. Idempotent; called by
    every store that opens the DB (~7× per ingest), so it fast-paths the common
    already-current case to a single `sqlite_master` check + a `PRAGMA` read.
    """
    has_videos = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='videos'"
    ).fetchone() is not None
    db_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if has_videos and db_version >= SCHEMA_VERSION:
        if db_version > SCHEMA_VERSION:
            logger.warning(
                "catalog DB is at schema v%d but this build only knows v%d — it was "
                "written by a newer version; proceeding (safe for additive schemas only).",
                db_version, SCHEMA_VERSION,
            )
        return
    # Fresh DB -> create the current schema. Older/partial DB -> CREATE IF NOT EXISTS
    # is a no-op for tables it already has (and picks up any wholly-new table).
    for ddl in ALL_TABLES:
        conn.execute(ddl)
    conn.commit()                           # persist base tables before the migration txns
    _run_migrations(conn)                   # ALTER in any columns an older DB lacks
    # Indexes LAST: an index on a migrated-in column would raise `no such column` if
    # built before its migration ran, so it must follow _run_migrations.
    for idx in INDEXES:
        conn.execute(idx)
    conn.commit()


# Busy timeout for every catalog connection. Openers of a not-yet-migrated DB
# serialize on the migration's `BEGIN IMMEDIATE` write lock; a data-backfill
# migration (invited by the recipe above) can outlast sqlite's 5s default, and the
# waiting opener must BLOCK for it rather than raise `database is locked`.
_BUSY_TIMEOUT_S = 60.0


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the central correlation DB with the standard setup every store needs:
    row access by name, **WAL + synchronous=NORMAL** (a reader and the ingest
    writer no longer block each other, and the write path does far fewer fsyncs),
    a generous busy timeout (so a slow migration doesn't make a concurrent opener
    error out), and the schema ensured once. Replaces the per-store
    `sqlite3.connect` + manual `apply_schema` boilerplate.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=_BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    apply_schema(conn)
    return conn
