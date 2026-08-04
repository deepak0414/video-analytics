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

from va.registry import embedder_id, get_text_embedder
from va.storage.vector.numpy_flat import NumpyFlatVectorStore, swap_shard

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


def index_text(video_id, video_dir, catalog_db, embedder=None, verify_exists=False,
               cfg=None) -> int:
    """(Re)build the `text_vectors` shard for one video. Returns rows indexed.
    `cfg`: the (footage-profile-overlaid) config to build+tag the embedder from —
    pass the same pin the caller runs its roles under (WS2.c); None = base config."""
    # Tag the shard with the embedder that ACTUALLY produced the vectors: from
    # config on the normal path; an INJECTED embedder must declare its own
    # `model_id`, else the shard is tagged "unknown" rather than risk a tag that
    # misdescribes the vectors (which would defeat the TAG-3 guard). Giving every
    # embedder a `model_id` so reprocess tags are always exact is a follow-up (RPRC-1).
    if embedder is None:
        embedder = get_text_embedder(cfg)
        tag = embedder_id("text_embedder", cfg)
    else:
        tag = getattr(embedder, "model_id", None) or "unknown"
    rows = _collect(catalog_db, video_id)
    vecs = embedder.embed([t for t, _ in rows]) if rows else None
    # Build to a TEMP shard and swap it in only on full success, so a failure ANYWHERE — embed,
    # a disk-full in np.savez, a process kill — leaves the prior shard, and thus text search,
    # intact (the same durability the visual reindex has). `_rebuild` has no dot so with_suffix
    # can't rewrite it.
    base = Path(video_dir) / "text_vectors"
    tmp = Path(video_dir) / "text_vectors_rebuild"
    for suf in (".npz", ".json"):           # clear any temp left by a prior crash
        p = tmp.with_suffix(suf)
        if p.exists():
            p.unlink()
    store = NumpyFlatVectorStore(tmp)
    if rows:
        store.add(vecs, [p for _, p in rows])
    store.set_meta({"embedder": tag})
    store.persist()
    # On the REPROCESS path (verify_exists — set by backfill_text_index), a concurrent `va remove`
    # during the embed deletes the catalog row + dir; persist() just recreated the dir. Re-check
    # right before the swap (as reindex_visual does) — swapping now would resurrect the removed
    # video in text search. Off by default: ingest's video always exists, and callers that index a
    # synthetic/uncataloged id (tagging tests) must not be rejected.
    if verify_exists:
        from va.storage.structured.catalog_sqlite import Catalog

        cat = Catalog(catalog_db)
        try:
            removed = cat.get(video_id) is None
        finally:
            cat.close()
        if removed:
            for suf in (".npz", ".json"):
                p = tmp.with_suffix(suf)
                if p.exists():
                    p.unlink()
            raise ValueError(
                f"video {video_id} was removed during reprocess — aborting text rebuild")
    swap_shard(tmp, base)
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
    # Rebuild under the config the video was INGESTED with (its recorded footage
    # profile), not the base config — else a profile's embedder override is
    # silently stripped by the very reprocess meant to refresh it (WS2.c).
    from va.configuration import config_for

    cfg = config_for(v.profile, v.source_type.value)
    # verify_exists: this is the reprocess/backfill path, so guard against a concurrent
    # `va remove` landing during the (possibly long) rebuild.
    return index_text(v.id, vdir, ws.catalog_db, embedder, verify_exists=True, cfg=cfg)
