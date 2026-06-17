"""Deterministic stub reranker — word-overlap, for tests/CI.

Scores each candidate by the fraction of query words it contains. Dependency-free
and deterministic, so the rerank plumbing (gather → rerank → sort → threshold)
can be tested offline. The real cross-encoder backend provides semantic relevance
beyond lexical overlap.
"""
from __future__ import annotations

import re
from typing import List, Sequence

_TOKEN = re.compile(r"[a-z0-9']+")


def _tokens(s: str) -> set[str]:
    return set(_TOKEN.findall(s.lower()))


class WordOverlapReranker:
    def rerank(self, query: str, candidates: Sequence[str]) -> List[float]:
        q = _tokens(query)
        if not q:
            return [0.0] * len(candidates)
        return [len(q & _tokens(c)) / len(q) for c in candidates]
