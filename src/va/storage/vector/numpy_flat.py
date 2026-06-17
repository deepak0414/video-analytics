"""Flat (brute-force) cosine-similarity vector store backed by numpy.

Exact nearest-neighbor, zero external deps — ideal for the PoC slice. Vectors
are L2-normalized on insert so cosine similarity is a single matrix-vector dot.
Persisted as a .npz (vectors) + .json (payloads) under the workdir.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .base import VectorHit


class NumpyFlatVectorStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._vecs: np.ndarray | None = None  # [N, D], L2-normalized
        self._payloads: list[dict[str, Any]] = []
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
            self._vecs = np.load(self._vec_file)["vectors"].astype(np.float32)
            self._payloads = json.loads(self._payload_file.read_text())

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        vecs = self._vecs if self._vecs is not None else np.zeros((0, 0), np.float32)
        np.savez(self._vec_file, vectors=vecs)
        self._payload_file.write_text(json.dumps(self._payloads))

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
