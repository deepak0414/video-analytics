"""Read-side vector index over per-video shards (layout v2).

Each video keeps its own `vectors.npz/.json` inside its directory; this class
presents them as ONE logical index: search every shard, merge by score. Writes
go directly to a per-video NumpyFlatVectorStore during ingest — removal of a
video is then just deleting its directory (no monolithic-index surgery).

At PoC scale (a few hundred vectors per video) load-per-search is fine; a
production engine (Milvus) replaces this with one collection + a video_id field.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from .base import VectorHit
from .numpy_flat import NumpyFlatVectorStore

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

    def _shards(self) -> List[NumpyFlatVectorStore]:
        if not self.videos_root.is_dir():
            return []
        return [
            _load_shard(npz)
            for npz in sorted(self.videos_root.glob(f"*/{self.shard_name}"))
        ]

    def search(self, query: np.ndarray, k: int) -> List[VectorHit]:
        hits: List[VectorHit] = []
        for shard in self._shards():
            hits.extend(shard.search(query, k))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]

    def count(self) -> int:
        return sum(s.count() for s in self._shards())

    def persist(self) -> None:  # shards persist themselves at write time
        pass
