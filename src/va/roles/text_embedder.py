"""Role (Retrieval Layer) — Text Embedder (the contract).

Embeds text into a semantic *text-text* space so a query can retrieve matching
caption / transcript / OCR / action text by cosine similarity — find by MEANING,
not keyword overlap. This is distinct from Role 2 (Visual Embedding), whose
SigLIP space is *image-text*: that one matches a query against frames; this one
matches a query against the words the pipeline extracted. Backends (hash stub,
sentence-transformer, remote NIM) are interchangeable as long as they satisfy
this Protocol. See the architecture doc's "Retrieval Layer" section.
"""
from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

import numpy as np


@runtime_checkable
class TextEmbedder(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Return [N, dim] float32, L2-normalized (cosine = dot product).

        Symmetric: the same method embeds both the documents (at ingest) and the
        query (at search) — they must share a space, like ingest/query must share
        the visual embedder."""
        ...
