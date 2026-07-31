"""Flat (brute-force) cosine-similarity vector store backed by numpy.

Exact nearest-neighbor, zero external deps — ideal for the PoC slice. Vectors
are L2-normalized on insert so cosine similarity is a single matrix-vector dot.
Persisted as a .npz (vectors) + .json (payloads) under the workdir.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from .base import VectorHit


def swap_shard(tmp_path: str | Path, final_path: str | Path) -> None:
    """Swap a freshly-built temp shard (`<tmp>.npz`/`.json`) into `<final>`. Replace `.json`
    BEFORE `.npz`: the sharded cache keys on the `.npz` mtime and `_load` needs both files, so
    making `.npz` the LAST file to change means a reader racing the swap sees the old pair or
    the fully-new pair — never a torn pair cached under the final mtime (the same invariant as
    `persist`, which writes `.json` then `.npz`). Building to a temp then swapping means a
    failure anywhere before the swap leaves the prior shard — and its search — intact."""
    tmp, final = Path(tmp_path), Path(final_path)
    os.replace(tmp.with_suffix(".json"), final.with_suffix(".json"))
    os.replace(tmp.with_suffix(".npz"), final.with_suffix(".npz"))


class NumpyFlatVectorStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._vecs: np.ndarray | None = None  # [N, D], L2-normalized
        self._payloads: list[dict[str, Any]] = []
        self._meta: dict[str, Any] | None = None  # shard-identity tag (embedder+dim)
        self._load()

    # --- persistence -------------------------------------------------------
    @property
    def _vec_file(self) -> Path:
        return self.path.with_suffix(".npz")

    @property
    def _payload_file(self) -> Path:
        return self.path.with_suffix(".json")

    def _load(self) -> None:
        if self._vec_file.exists() and self._payload_file.exists():
            npz = np.load(self._vec_file)
            self._vecs = npz["vectors"].astype(np.float32)
            if "meta" in npz.files:  # identity tag; absent on pre-tagging shards
                self._meta = json.loads(npz["meta"].item())
            self._payloads = json.loads(self._payload_file.read_text())

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        vecs = self._vecs if self._vecs is not None else np.zeros((0, 0), np.float32)
        arrays: dict[str, np.ndarray] = {"vectors": vecs}
        if self._meta is not None:
            # the vectors are the source of truth for `dim`; stamp it at write time
            arrays["meta"] = np.array(json.dumps({**self._meta, "dim": self.dim}))
        # Write the payloads (.json) BEFORE the vectors (.npz). `_load` gates on BOTH files
        # existing, and the sharded shard-cache keys on the .npz mtime — so making .npz the
        # LAST file to appear means a concurrent reader mid-write either sees no .npz (skips
        # the shard) or a .npz whose .json is already present (a consistent pair). Writing
        # .npz first can cache an empty/torn shard under the final mtime, silently dropping
        # the video from search until the next rebuild or restart.
        self._payload_file.write_text(json.dumps(self._payloads))
        np.savez(self._vec_file, **arrays)

    # --- ops ---------------------------------------------------------------
    @staticmethod
    def _normalize(m: np.ndarray) -> np.ndarray:
        m = np.atleast_2d(m).astype(np.float32)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return m / norms

    def add(self, vectors: np.ndarray, payloads: list[dict[str, Any]]) -> None:
        vectors = self._normalize(vectors)
        if vectors.shape[0] != len(payloads):
            raise ValueError("vectors and payloads length mismatch")
        if self._vecs is None:
            self._vecs = vectors
        else:
            if vectors.shape[1] != self._vecs.shape[1]:
                raise ValueError("embedding dimension mismatch with existing store")
            self._vecs = np.vstack([self._vecs, vectors])
        self._payloads.extend(payloads)

    def search(self, query: np.ndarray, k: int) -> list[VectorHit]:
        if self._vecs is None or len(self._payloads) == 0:
            return []
        q = self._normalize(query)[0]
        scores = self._vecs @ q  # cosine, since both normalized
        k = min(k, scores.shape[0])
        # argpartition for top-k, then sort those k.
        idx = np.argpartition(-scores, k - 1)[:k]
        idx = idx[np.argsort(-scores[idx])]
        return [VectorHit(payload=self._payloads[i], score=float(scores[i])) for i in idx]

    def count(self) -> int:
        return len(self._payloads)

    # --- shard identity tag (embedder + dim) -------------------------------
    @property
    def dim(self) -> int | None:
        """Embedding dimension of the stored vectors (None if the shard is empty)."""
        if self._vecs is not None and self._vecs.shape[0] > 0:
            return int(self._vecs.shape[1])
        return None

    @property
    def meta(self) -> dict[str, Any] | None:
        """Identity tag persisted with the shard (embedder + dim), or None for a
        shard written before tagging existed."""
        return self._meta

    def set_meta(self, meta: dict[str, Any]) -> None:
        """Tag this shard with identity metadata (e.g. the embedder that produced
        it). `dim` is added from the vectors at persist() time — the array is the
        source of truth. Stored inside the .npz so it moves/deletes with the shard."""
        self._meta = dict(meta)
