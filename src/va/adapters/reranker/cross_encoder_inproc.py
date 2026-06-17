"""Real Retrieval-Layer backend: cross-encoder reranker via transformers.

A sequence-classification cross-encoder (default BAAI/bge-reranker-v2-m3 — the
multilingual reranker matching bge-m3) that scores (query, candidate) pairs with
a single relevance logit. Uses `transformers` directly (NOT FlagEmbedding /
sentence-transformers, which drag in torchcodec — dead on this aarch64 box).
Loaded once via the ModelManager. Requires the `rerank` extra. Select via config:
reranker.model = <HF id>. NV-rerankqa would slot in later as an `http` backend.
"""
from __future__ import annotations

from typing import Any, List, Sequence

from va.runtime.device import resolve_device
from va.runtime.manager import MANAGER

_DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
_BATCH = 32


class CrossEncoderReranker:
    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.weights = load.get("weights") or load.get("model") or _DEFAULT_MODEL
        self.device = resolve_device(load.get("device"))
        self._tok, self._model = MANAGER.get(
            f"reranker::{self.weights}::{self.device}", self._build
        )

    def _build(self):
        from transformers import (  # deferred heavy import
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        tok = AutoTokenizer.from_pretrained(self.weights)
        model = (AutoModelForSequenceClassification
                 .from_pretrained(self.weights).to(self.device).eval())
        return tok, model

    def rerank(self, query: str, candidates: Sequence[str]) -> List[float]:
        import torch

        candidates = list(candidates)
        if not candidates:
            return []
        scores: List[float] = []
        for i in range(0, len(candidates), _BATCH):
            batch = candidates[i:i + _BATCH]
            pairs = [[query, c] for c in batch]
            enc = self._tok(pairs, padding=True, truncation=True, max_length=512,
                            return_tensors="pt").to(self.device)
            with torch.no_grad():
                logits = self._model(**enc).logits.view(-1).float()
            scores.extend(logits.cpu().tolist())
        return scores
