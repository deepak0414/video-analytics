"""Read-side vector index over per-video shards (layout v2).

Each video keeps its own `vectors.npz/.json` inside its directory; this class
presents them as ONE logical index: search every shard, merge by score. Writes
go directly to a per-video NumpyFlatVectorStore during ingest — removal of a
video is then just deleting its directory (no monolithic-index surgery).

At PoC scale (a few hundred vectors per video) load-per-search is fine; a
production engine (Milvus) replaces this with one collection + a video_id field.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from .base import VectorHit
from .numpy_flat import NumpyFlatVectorStore

logger = logging.getLogger(__name__)


def _compatible(shard: NumpyFlatVectorStore, expect_embedder: str, qdim: int | None) -> bool:
    """Is this shard safe to search for the current query? A TAGGED shard must match
    the embedder AND dim; an UNTAGGED (pre-tagging) shard can't be verified by model,
    so it is admitted only when its dim matches the query — enough to avoid a
    dimension-mismatch crash, though a same-dim foreign-model legacy shard can't be
    caught (the honest D4 gap; TAG-4 backfill records dim to narrow it)."""
    meta = shard.meta
    if meta is None:
        return shard.dim is None or qdim is None or shard.dim == qdim
    if meta.get("embedder") != expect_embedder:
        return False
    mdim = meta.get("dim")
    # an empty shard (dim None) is a no-op to search — admit it on an embedder match
    return mdim is None or qdim is None or mdim == qdim


# Process-level cache of loaded shards. `query()` calls `count()` then `search()`,
# and each rebuilds every shard from disk (np.load + json parse) — so the whole
# corpus is re-read TWICE per query. Cache the loaded store per shard file, keyed
# by its mtime (ns): a re-ingest rewrites the .npz -> new mtime -> automatic
# reload; `va remove` deletes the dir -> the glob no longer yields it. Held for the
# process lifetime, which is the win for the long-lived web server (the CLI, one
# query per process, still benefits: count()+search() now load once, not twice).
_SHARD_CACHE: Dict[str, Tuple[int, NumpyFlatVectorStore]] = {}


def _load_shard(npz: Path) -> NumpyFlatVectorStore:
    key = str(npz)
    mtime = npz.stat().st_mtime_ns
    cached = _SHARD_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    store = NumpyFlatVectorStore(npz.with_suffix(""))
    _SHARD_CACHE[key] = (mtime, store)
    return store


def clear_shard_cache() -> None:
    """Drop the in-process shard cache (tests / explicit invalidation)."""
    _SHARD_CACHE.clear()


class ShardedVectorStore:
    def __init__(self, videos_root: str | Path, shard_name: str = "vectors.npz"):
        # shard_name lets a second logical index live alongside the visual one —
        # e.g. "text_vectors.npz" for the Retrieval Layer's semantic text index.
        self.videos_root = Path(videos_root)
        self.shard_name = shard_name
        self.skipped = 0   # shards skipped by the last search's embedder guard

    def _shards(self) -> List[NumpyFlatVectorStore]:
        if not self.videos_root.is_dir():
            return []
        return [
            _load_shard(npz)
            for npz in sorted(self.videos_root.glob(f"*/{self.shard_name}"))
        ]

    def search(self, query: np.ndarray, k: int,
               expect_embedder: str | None = None) -> List[VectorHit]:
        """Search every shard and merge by score. With `expect_embedder`, shards whose
        identity tag doesn't match the current embedder are SKIPPED rather than mixed
        in (or crashed on, for a dimension mismatch); the skipped count is recorded on
        `self.skipped` and logged."""
        qdim = int(np.atleast_2d(query).shape[1]) if np.size(query) else None
        hits: List[VectorHit] = []
        self.skipped = 0
        for shard in self._shards():
            if expect_embedder is not None and not _compatible(shard, expect_embedder, qdim):
                self.skipped += 1
                continue
            hits.extend(shard.search(query, k))
        if self.skipped:
            logger.warning(
                "vector search skipped %d shard(s) not on embedder %r — reprocess them "
                "so they rejoin the index (provenance-reprocess-plan.md).",
                self.skipped, expect_embedder,
            )
        hits.sort(key=lambda h: -h.score)
        return hits[:k]

    def count(self, expect_embedder: str | None = None,
              expect_dim: int | None = None) -> int:
        """Total vectors across shards. With `expect_embedder`, counts only shards
        compatible with that embedder AND dim — pass `expect_dim` (the current query
        dim) so this agrees with the search-time guard even on untagged shards; a
        stale/mismatched index then reads as unusable (0), not merely non-empty."""
        shards = self._shards()
        if expect_embedder is not None:
            shards = [s for s in shards if _compatible(s, expect_embedder, expect_dim)]
        return sum(s.count() for s in shards)

    def persist(self) -> None:  # shards persist themselves at write time
        pass
