"""Retrieval Layer (SR.2) — build the semantic text index for a video.

Reads the video's text from the four text modalities — Role 4 captions, Role 8
transcript lines, Role 10 OCR strings, Role 7 action labels — embeds them with
the configured `TextEmbedder`, and writes a per-video `text_vectors` shard
alongside the visual `vectors` shard (so it inherits remove/reingest for free).
Dedups identical text per modality (OCR repeats the same string a lot).
Idempotent: rebuilds the shard from scratch each call (also usable as a backfill).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from va.registry import get_text_embedder
from va.storage.vector.numpy_flat import NumpyFlatVectorStore

# (modality string, source_role, SQL) per text modality.
_SOURCES = [
    ("caption", 4,
     "SELECT start_time AS ts, end_time AS te, caption AS text FROM segments "
     "WHERE video_id=? AND caption IS NOT NULL AND TRIM(caption) <> ''"),
    ("transcript", 8,
     "SELECT start_time AS ts, end_time AS te, text FROM transcripts WHERE video_id=?"),
    ("on_screen_text", 10,
     "SELECT timestamp AS ts, timestamp AS te, text FROM ocr_results WHERE video_id=?"),
    ("action", 7,
     "SELECT start_time AS ts, end_time AS te, action_class AS text "
     "FROM action_events WHERE video_id=?"),
]


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _collect(catalog_db, video_id) -> list[tuple[str, dict]]:
    conn = sqlite3.connect(str(catalog_db))
    conn.row_factory = sqlite3.Row
    vid = str(video_id)
    seen: dict[tuple[str, str], tuple] = {}  # (modality, normtext) -> row, earliest kept
    try:
        for modality, role, sql in _SOURCES:
            for r in conn.execute(sql, (vid,)):
                text = (r["text"] or "").strip()
                if not text:
                    continue
                ts = float(r["ts"] or 0.0)
                te = float(r["te"] if r["te"] is not None else ts)
                key = (modality, _norm(text))
                if key in seen and seen[key][2] <= ts:
                    continue
                seen[key] = (modality, role, ts, te, text)
    finally:
        conn.close()
    rows: list[tuple[str, dict]] = []
    for modality, role, ts, te, text in seen.values():
        rows.append((text, {
            "video_id": vid, "modality": modality, "source_role": role,
            "time_start": ts, "time_end": te, "text": text,
        }))
    return rows


def index_text(video_id, video_dir, catalog_db, embedder=None) -> int:
    """(Re)build the `text_vectors` shard for one video. Returns rows indexed."""
    embedder = embedder or get_text_embedder()
    rows = _collect(catalog_db, video_id)
    store_path = Path(video_dir) / "text_vectors"
    for suf in (".npz", ".json"):  # idempotent: start fresh
        p = store_path.with_suffix(suf)
        if p.exists():
            p.unlink()
    store = NumpyFlatVectorStore(store_path)
    if rows:
        vecs = embedder.embed([t for t, _ in rows])
        store.add(vecs, [p for _, p in rows])
    store.persist()
    return len(rows)


def backfill_text_index(workdir: str, ident: str, embedder=None) -> Optional[int]:
    """Build the text index for an already-ingested video (no reingest)."""
    from va.pipeline.manage import lookup_video
    from va.pipeline.paths import Workspace
    from va.storage.structured.catalog_sqlite import Catalog

    ws = Workspace(workdir)
    cat = Catalog(ws.catalog_db)
    try:
        v = lookup_video(cat, ident)
    finally:
        cat.close()
    if v is None:
        return None
    vdir = ws.video_dir(v.source_key, v.title, create=True)
    return index_text(v.id, vdir, ws.catalog_db, embedder)
