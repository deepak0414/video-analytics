"""Retrieval Layer (SR.2) — semantic text search over the `text_vectors` shards.

Embeds the query with the same `TextEmbedder` used at ingest and cosine-searches
the per-video text index: find caption / transcript / OCR / action text by
MEANING, not keyword overlap ("the budget" surfaces "our fiscal spending"). This
is the semantic counterpart to the per-modality word-overlap `search()` methods
the stores still keep as the offline-stub fallback. SR.4 fuses these hits with
visual + structured retrieval and reranks them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence
from uuid import UUID

from va.pipeline.paths import Workspace
from va.registry import get_text_embedder
from va.storage.vector.sharded import ShardedVectorStore


@dataclass
class TextHit:
    video_id: UUID
    modality: str           # caption | transcript | on_screen_text | action
    time_start: float
    time_end: float
    text: str
    source_role: int
    score: float


def search_text(
    query: str, workdir: str = ".va", k: int = 10,
    modalities: Optional[Sequence[str]] = None,
) -> List[TextHit]:
    ws = Workspace(workdir)
    store = ShardedVectorStore(ws.videos_root, shard_name="text_vectors.npz")
    if store.count() == 0:
        return []
    qv = get_text_embedder().embed([query])
    fetch = k * 5 if modalities else k  # over-fetch when filtering by modality
    out: List[TextHit] = []
    for h in store.search(qv, k=fetch):
        p = h.payload
        if modalities and p.get("modality") not in modalities:
            continue
        out.append(TextHit(
            video_id=UUID(p["video_id"]), modality=p.get("modality", ""),
            time_start=float(p.get("time_start", 0.0)),
            time_end=float(p.get("time_end", 0.0)),
            text=p.get("text", ""), source_role=int(p.get("source_role", 0)),
            score=h.score,
        ))
    return out[:k]
