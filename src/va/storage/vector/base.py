"""Vector store interface (Principle P5).

Start with a numpy flat index (numpy_flat.py); swap to Milvus/Qdrant behind this
same interface later with no caller changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np


@dataclass
class VectorHit:
    payload: dict[str, Any]
    score: float  # cosine similarity in [-1, 1]


class VectorStore(Protocol):
    def add(self, vectors: np.ndarray, payloads: list[dict[str, Any]]) -> None:
        """Add N vectors (shape [N, D]) with one payload dict each."""
        ...

    def search(self, query: np.ndarray, k: int) -> list[VectorHit]:
        """Return up to k nearest payloads by cosine similarity."""
        ...

    def count(self) -> int:
        ...

    def persist(self) -> None:
        ...
