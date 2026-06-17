"""Role 2 — Visual Embedding Model (the contract).

Embeds images and text into the SAME vector space so a text query can retrieve
matching frames by cosine similarity. Backends (hash stub, SigLIP, remote) are
interchangeable as long as they satisfy this Protocol.
"""
from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

import numpy as np
from PIL import Image


@runtime_checkable
class VisualEmbedder(Protocol):
    dim: int

    def embed_image(self, images: Sequence[Image.Image]) -> np.ndarray:
        """Return [N, dim] float32, L2-normalized."""
        ...

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        """Return [N, dim] float32, L2-normalized, comparable to image vectors."""
        ...
