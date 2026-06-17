"""Real Retrieval-Layer backend: semantic text embeddings via transformers.

Uses HuggingFace `transformers` directly (AutoModel + pooling), NOT
sentence-transformers — the installed ST is a multimodal build that hard-imports
`torchcodec`, which fails on this aarch64 box (no FFmpeg shared libs; same issue
as Role 9). BGE/E5 are plain transformers encoders, so this needs only
`transformers` (already present via the siglip/qwenvl extras).

Default BAAI/bge-small-en-v1.5 (384-d, strong, permissive, **CLS** pooling).
Vendor-neutral; NV-embedqa would slot in later as an `http` backend behind the
same TextEmbedder Protocol. Select via config: text_embedder.model = <HF id>.
Loaded once via the ModelManager. Requires the `text-embed` extra.
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from va.runtime.device import resolve_device
from va.runtime.manager import MANAGER

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_BATCH = 64


class HFTextEmbedder:
    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.weights = load.get("weights") or load.get("model") or _DEFAULT_MODEL
        self.device = resolve_device(load.get("device"))
        # BGE wants CLS pooling; E5/most others want mean. Default CLS (default model is BGE).
        self.pooling = str(load.get("pooling", "cls")).lower()
        self._tok, self._model = MANAGER.get(
            f"textemb::{self.weights}::{self.device}", self._build
        )
        self.dim = int(self._model.config.hidden_size)

    def _build(self):
        from transformers import AutoModel, AutoTokenizer  # deferred heavy import

        tok = AutoTokenizer.from_pretrained(self.weights)
        model = AutoModel.from_pretrained(self.weights).to(self.device).eval()
        return tok, model

    def _pool(self, last_hidden, mask):
        import torch

        if self.pooling == "mean":
            m = mask.unsqueeze(-1).type_as(last_hidden)
            return (last_hidden * m).sum(1) / m.sum(1).clamp(min=1e-9)
        return last_hidden[:, 0]  # CLS

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        import torch

        texts = list(texts)
        if not texts:
            return np.empty((0, self.dim), np.float32)
        out = []
        for i in range(0, len(texts), _BATCH):
            batch = texts[i:i + _BATCH]
            enc = self._tok(batch, padding=True, truncation=True, max_length=512,
                            return_tensors="pt").to(self.device)
            with torch.no_grad():
                hidden = self._model(**enc).last_hidden_state
            vec = self._pool(hidden, enc["attention_mask"])
            vec = torch.nn.functional.normalize(vec, p=2, dim=1)
            out.append(vec.cpu().numpy())
        return np.vstack(out).astype(np.float32)
