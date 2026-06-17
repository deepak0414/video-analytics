"""Real Role-4 backend: Qwen2.5-VL (multi-frame, video-native VLM).

Captions a segment from its keyframe images. Loaded once via the ModelManager.
Requires the `qwenvl` extra (transformers + accelerate + qwen-vl-utils). Select
via config: vlm_captioner.model = qwen2.5-vl-7b. The same model can later serve
Role 11 (reasoning).
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence

from PIL import Image

from va.roles.vlm_captioner import DEFAULT_PROMPT
from va.runtime.device import resolve_device
from va.runtime.manager import MANAGER

_WEIGHTS = {
    "qwen2.5-vl-7b": "Qwen/Qwen2.5-VL-7B-Instruct",
    "qwen2.5-vl-3b": "Qwen/Qwen2.5-VL-3B-Instruct",
}


class QwenCaptioner:
    def __init__(self, load: dict[str, Any] | None = None):
        load = load or {}
        self.model_key = load.get("model", "qwen2.5-vl-7b")
        self.weights = load.get("weights", _WEIGHTS.get(self.model_key, self.model_key))
        self.device = resolve_device(load.get("device"))
        self.max_new_tokens = int(load.get("max_new_tokens", 96))
        bundle = MANAGER.get(f"qwenvl::{self.weights}::{self.device}", self._build)
        self._model = bundle["model"]
        self._processor = bundle["processor"]

    def _build(self) -> dict:
        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        dtype = torch.float16 if self.device != "cpu" else torch.float32
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.weights, torch_dtype=dtype
        ).to(self.device).eval()
        processor = AutoProcessor.from_pretrained(self.weights)
        return {"model": model, "processor": processor}

    def caption(self, images: Sequence[Image.Image], prompt: Optional[str] = None) -> str:
        import torch

        imgs: List[Image.Image] = [im.convert("RGB") for im in images]
        content = [{"type": "image", "image": im} for im in imgs]
        content.append({"type": "text", "text": prompt or DEFAULT_PROMPT})
        messages = [{"role": "user", "content": content}]

        text = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self._processor(text=[text], images=imgs, return_tensors="pt").to(self.device)
        with torch.no_grad():
            # do_sample=False: deterministic captions (deep-scan counting + tests
            # depend on repeatable descriptions of identical frames).
            generated = self._model.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False
            )
        # strip the prompt tokens, decode only the completion
        trimmed = [out[len(inp):] for inp, out in zip(inputs.input_ids, generated)]
        out = self._processor.batch_decode(
            trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=True
        )[0]
        return out.strip()
