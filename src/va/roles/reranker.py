"""Role (Retrieval Layer) — Reranker (the contract).

A cross-encoder that reads the query and a candidate TOGETHER and scores true
relevance — the highest-leverage retrieval-quality step. Bi-encoders (the text
embedder, SigLIP) embed query and candidate separately and compare; a reranker
attends across both, so it catches relevance a cosine score misses. The retriever
(SR.4) gathers candidates cheaply (vector search), then reranks the shortlist.
Backends (word-overlap stub, cross-encoder, remote NIM) are interchangeable.
"""
from __future__ import annotations

from typing import List, Protocol, Sequence, runtime_checkable


@runtime_checkable
class Reranker(Protocol):
    def rerank(self, query: str, candidates: Sequence[str]) -> List[float]:
        """Return a relevance score per candidate, ALIGNED to input order
        (higher = more relevant). The caller sorts / thresholds. Scores are not
        calibrated across backends — only the ranking they induce is meaningful."""
        ...
