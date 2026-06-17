"""Deterministic stub text embedder — for tests/CI without a model download.

A signed bag-of-words **hashing embedder**: each token is hashed to a bucket
(with a sign to cut collisions) and accumulated; the vector is L2-normalized. So
two texts that share words land close (positive cosine) and disjoint texts land
near-orthogonal — a real, meaningful *lexical* retrieval signal that the whole
retrieval-layer plumbing can be tested against offline.

It is NOT semantic: paraphrases with no shared words won't match (that's exactly
what the real sentence-transformer backend adds). The stub exists so ingest →
index → retrieve runs deterministically with no weights, no GPU, no network.
"""
from __future__ import annotations

import hashlib
import re
from typing import Sequence

import numpy as np

_TOKEN = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class HashTextEmbedder:
    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), np.float32)
        for i, text in enumerate(texts):
            for tok in _tokens(text):
                h = hashlib.sha256(tok.encode()).digest()
                idx = int.from_bytes(h[:8], "big") % self.dim
                sign = 1.0 if (h[8] & 1) else -1.0  # signed hashing trick
                out[i, idx] += sign
            n = float(np.linalg.norm(out[i]))
            if n:
                out[i] /= n
        return out
