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

import sqlite3
from pathlib import Path

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

# Index video_id on the high-volume per-frame tables for correlation joins.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_segments_video ON segments(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_detections_video_ts ON object_detections(video_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_actions_video ON action_events(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_transcripts_video ON transcripts(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_ocr_video ON ocr_results(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_observations_key ON observations(video_id, prompt_key, timestamp)",
]

ALL_TABLES = [
    VIDEOS, SEGMENTS, OBJECT_TRACKS, OBJECT_DETECTIONS,
    ACTION_EVENTS, TRANSCRIPTS, OCR_RESULTS, OBSERVATIONS,
]

# Columns added to `videos` after the initial schema. A DB created before the
# column existed still has the old table, and the fast path below skips the
# CREATE (which is `IF NOT EXISTS` anyway), so these must be ensured via ALTER
# (SQLite has no `ADD COLUMN IF NOT EXISTS`). Additive + nullable only.
_VIDEOS_ADDED_COLUMNS = {
    "last_ingest_run_id": "TEXT",
}


def _ensure_videos_columns(conn: sqlite3.Connection) -> None:
    """Backfill additive columns onto a pre-existing `videos` table. Cheap: one
    PRAGMA read, and the ALTER runs only the first time a given DB is opened."""
    have = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
    added = False
    for col, decl in _VIDEOS_ADDED_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {col} {decl}")
            added = True
    if added:
        conn.commit()


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create every table + index (idempotent). Run by any store that opens the DB.

    Fast path: all tables are created together, so if `videos` already exists the
    schema is present — skip the 14 DDL + commit (but still ensure additive column
    migrations). This runs on EVERY store open (and a store opens the DB ~7× per
    ingest), so the check pays for itself.
    """
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='videos'"
    ).fetchone() is not None:
        _ensure_videos_columns(conn)
        return
    for ddl in ALL_TABLES:
        conn.execute(ddl)
    for idx in INDEXES:
        conn.execute(idx)
    conn.commit()


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the central correlation DB with the standard setup every store needs:
    row access by name, **WAL + synchronous=NORMAL** (a reader and the ingest
    writer no longer block each other, and the write path does far fewer fsyncs),
    and the schema ensured once. Replaces the per-store `sqlite3.connect` + manual
    `apply_schema` boilerplate.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    apply_schema(conn)
    return conn
