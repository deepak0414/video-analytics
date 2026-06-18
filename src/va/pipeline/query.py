"""Query pipeline (Tier 1): text -> embed -> vector search -> ranked moments.

Joins each vector hit's payload back to the catalog so results carry the source
video's URI for display/navigation.
"""
from __future__ import annotations

from uuid import UUID

from va.contracts.embedding import SearchHit
from va.pipeline.paths import Workspace
from va.registry import get_visual_embedder
from va.storage.structured.catalog_sqlite import Catalog
from va.storage.vector.sharded import ShardedVectorStore


def query(text: str, workdir: str = ".va", k: int = 10) -> list[SearchHit]:
    ws = Workspace(workdir)
    # layout v2: one logical index over per-video shards
    store = ShardedVectorStore(ws.videos_root)
    if store.count() == 0:
        return []

    embedder = get_visual_embedder()
    qvec = embedder.embed_text([text])[0]
    raw_hits = store.search(qvec, k=k)

    catalog = Catalog(ws.catalog_db)
    try:
        # Resolve every hit's source video in ONE query (distinct video_ids), not
        # a SELECT per hit. Prefer the live catalog source_uri (in case it
        # changed); fall back to the payload's stored uri.
        vids = {h.payload["video_id"] for h in raw_hits if h.payload.get("video_id")}
        videos = catalog.get_many([UUID(v) for v in vids])
        results: list[SearchHit] = []
        for h in raw_hits:
            p = h.payload
            v = videos.get(p.get("video_id"))
            source_uri = v.source_uri if v is not None else p.get("source_uri", "")
            results.append(
                SearchHit(
                    video_id=UUID(p["video_id"]),
                    source_uri=source_uri,
                    timestamp=float(p["timestamp"]),
                    score=h.score,
                )
            )
        return results
    finally:
        catalog.close()
