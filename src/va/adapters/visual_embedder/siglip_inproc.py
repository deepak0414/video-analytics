"""Real Role-2 backend: SigLIP SO400M via transformers.

Loads through the shared ModelManager (never directly), so the model is built
once and reused. Requires the `siglip` extra (torch + transformers); import is
deferred so the rest of the package runs without those heavy deps installed.
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np
from PIL import Image

from va.runtime.device import resolve
from va.runtime.manager import MANAGER


class SiglipEmbedder:
    def __init__(self, load: dict[str, Any]):
        self.load = resolve(load)
        self.weights = self.load.get("weights", "google/siglip-so400m-patch14-384")
        self.device = self.load["device"]
        bundle = MANAGER.get(f"siglip::{self.weights}::{self.device}", self._build)
        self._model = bundle["model"]
        self._processor = bundle["processor"]
        self.dim = int(self._model.config.text_config.hidden_size)

    def _build(self) -> dict:
        import torch  # noqa: F401
        from transformers import AutoModel, AutoProcessor

        model = AutoModel.from_pretrained(self.weights).to(self.device).eval()
        processor = AutoProcessor.from_pretrained(self.weights)
        return {"model": model, "processor": processor}

    @staticmethod
    def _normalize(m: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(m, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return (m / n).astype(np.float32)

    @staticmethod
    def _to_numpy(out) -> np.ndarray:
        """Unwrap get_*_features output to a feature tensor across transformers
        versions (it may return a bare tensor or an output object)."""
        import torch

        if not isinstance(out, torch.Tensor):
            for attr in ("image_embeds", "text_embeds", "pooler_output"):
                val = getattr(out, attr, None)
                if val is not None:
                    out = val
                    break
            else:
                raise TypeError(f"unexpected SigLIP feature output: {type(out)}")
        return out.detach().cpu().numpy()

    def embed_image(self, images: Sequence[Image.Image]) -> np.ndarray:
        import torch

        inputs = self._processor(images=list(images), return_tensors="pt").to(self.device)
        with torch.no_grad():
            feats = self._model.get_image_features(**inputs)
        return self._normalize(self._to_numpy(feats))

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        import torch

        inputs = self._processor(
            text=list(texts), return_tensors="pt", padding="max_length"
        ).to(self.device)
        with torch.no_grad():
            feats = self._model.get_text_features(**inputs)
        return self._normalize(self._to_numpy(feats))
